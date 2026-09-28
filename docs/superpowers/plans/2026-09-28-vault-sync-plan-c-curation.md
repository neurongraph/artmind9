# Vault sync — Plan C (phase 4: curation travels) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The graph is rebuildable from vault files alone (spec D6): an `artmind update` fact, a detected conflict and its resolution, and a `same_as.yaml` edit all reach a machine with its own local Neo4j through `artmind vault sync`, and `vault status` counts them while they are pending.

**Architecture:** Spec [2026-09-26-vault-git-transport-design.md](../specs/2026-09-26-vault-git-transport-design.md) §7, §12 item 4, §14 A6. `update confirm` writes a staging folder (`kg/<domain>/update__<session>__<draft>/`) and commits the graph *from* it through `ingest._commit_document_tx` — the transaction `vault sync`'s track A already replays folders with — as a `:UserChat`. Graph-only curation becomes one JSON file per record under `.artmind/data/curation/<kind>/<id>.json` (`artmind/curation_records.py`, first kind `artmind/conflict_records.py`), applied by a new `vault sync` track C. Track D diffs `.artmind/same_as.yaml` between the bookmark and HEAD and rebuilds exactly the members of changed groups, with the groups as committed at HEAD. Plan C2 (`2026-09-28-vault-sync-plan-c2-curation-kinds.md`) builds on this plan's curation-record framework.

**Verified:** every code block in this plan was applied, in task order, by a script that parses this file, to a scratch worktree of `e9f7a9f` (branch `vault-sync-cde`), and the full suite was run after each task — see "Verification record" at the end. The "Expected: FAIL" claims come from reverting each task's non-test files and re-running its tests.

**Tech Stack:** Python 3.14, Click (rich-click), neo4j driver, pytest (`uv run --group dev pytest test/ -q`, ~70 s, `2440 passed, 14 skipped` at `e9f7a9f`), real throwaway git repos in `tmp_path` for anything git decides, recording fakes for every Neo4j seam.

---

## Context for the implementer

Read before starting:

- `CLAUDE.md`, "Testing implications" §1–§4. §3 above all: when a test fakes a graph write, assert on **what was sent** (which Cypher, which parameters), never on a count.
- The spec above: §6 (apply), §7 (making graph-only writes travel), §14 A4–A6.
- `artmind/vault_sync.py` top to bottom, and `test/test_vault_sync.py`'s helpers, which later tasks reuse by name: `repo` (a `git init`ed `tmp_path`), `_commit_all`, `_write_doc_folder`, `_patch_kg_dir`, `_patch_structured_text_dir`, `_patch_ingest_and_projection` (records `write_to_neo4j`, `retract_document`, `rebuild_in_batches`, `sweeps`), `_bookmarks`, the autouse `graph` fixture (`_FakeGraphState`: bookmarks and `Document` fingerprints in memory), `_current`.
- `test/conftest.py`: an autouse fixture replaces `artmind.graph_query.neo4j_session` with a null session (every query returns no rows) and repoints `ARTMIND_HOME`/`ARTMIND_DATA_DIR` at a temp dir. So code resolves the session **at call time** through the module that owns it, and tests repoint `paths.KG_DIR` / `paths.CURATION_DIR` / `same_as.SAME_AS_PATH` inside the test vault when git must see the files.
- `test/test_no_git_writes.py`: artmind never writes to the vault's git (D1/D2). Nothing in this plan runs a git write; files are written and Obsidian Git commits them.

Terminology:

- **Track A / B**: `vault sync` replaying KG document folders / restoring structured tables (Plans A, B).
- **Track C** (new): applying curation records — `.artmind/data/curation/<kind>/<id>.json` — added or changed in `bookmark..HEAD`, and removing deleted ones.
- **Track D** (new): rebuilding the members of same-as groups added, removed or changed in `.artmind/same_as.yaml` in `bookmark..HEAD`.
- **Fingerprint**: Plan B's sha256 over a staging folder's committed files (`sync_state.staging_fingerprint`); a curation record's fingerprint is sha256 of its file bytes.

**Line numbers** are those of each file as it stands when the task starts. Every replacement quotes the exact old code: match on the text, not the number.

Run the suite with:

```bash
uv run --group dev pytest test/ -q
```

Do **not** run `just dev-install`, `artmind init` outside `tmp_path`, or anything against a live Neo4j. The user tests live afterwards ("Live verification (user)" at the end).

## Decisions this plan makes (the spec left them open)

Each is argued under "Self-review → Design decisions the spec did not settle"; the number in brackets is its number there.

1. **One commit path for `update`** [1]. The old direct write and `_commit_document_tx` did not produce the same graph: the chat wrote a `:UserChat` + `MERGE (o:Observation) SET o = $props` + `EXTRACTED_FROM`→`UserChat`, with no prior-version demotion, no fingerprint, and a rebuild that ignored same-as groups touching its keys. So `update confirm` now writes the staging folder, reads it back with `ingest._load_staged` (exactly what `vault sync` reads), and commits it with `ingest._commit_document_tx`, which learns one variant: a `document.json` with `source_kind: "user_chat"` commits as a `:UserChat` (its `raw_text` and session fields) instead of a `:Document` with chunks, and its observations are `EXTRACTED_FROM` it. The `:UserChat` stays: vector and full-text search, `update export` and the structural census all read it.
2. **One folder per confirmed draft, not per session** [2]. A session can confirm several drafts, so the folder is `kg/<domain>/update__<session_id>__<draft_id>/` and the UserChat/document id `update:<session_id>:<draft_id>`. Deterministic: a retried confirm of the same draft rewrites the same folder and MERGEs onto the same nodes. Collision-free across machines: `session_id` is a uuid4 minted by `update draft`, `draft_id` the draft's row id in the registry of the one machine the session lives on.
3. **Retracting an update** [3]. A new `artmind update retract --session S [--draft N]` retracts in the graph first (`ingest.retract_document`, which now also moves a `:UserChat` to a new `:UserChatHistory` label), rebuilds the touched keys with their whole same-as groups, then deletes the folder. The deletion is what travels; any machine's `vault sync` retracts the same UserChat through track A's existing retraction path. Deleting the folder by hand works the same way.
4. **One file per curation record** [4], never a shared `conflicts.yaml`: every `.artmind/data/**` path is `merge=binary`, so a shared file would make two machines' independent curation a whole-file conflict that blocks `vault sync`. Records are written atomically (temp file + `os.replace`) and serialised deterministically (sorted keys), so an unchanged record never churns in git.
5. **A generic curation-record framework** [5] (`curation_records.Kind`: `apply`, `remove`, `read_fingerprints`, `domains`, `phase`), with conflicts as its first kind. Plan C2 adds four more kinds without touching `vault sync` again. `phase: "post"` kinds (conflicts) apply after the union rebuild — `CONFLICTS_WITH` joins two rebuilt `:Entity` nodes; `"pre"` kinds apply before it and add keys to it.
6. **The vault's record wins over a fresh detection** [6]. `materialize` never rewrites an existing record file — re-detecting a conflict another machine recorded (and perhaps resolved) neither reopens it nor churns it. A conflict detected before records existed keeps its node's fields (the old `ON CREATE` semantics) when its first record is written. Applying a record SETs every field: the file is the truth.
7. **A status conflict between machines** [7] can only arise when two machines write the same record before either pulls: git reports a conflict on that one file, `vault sync` refuses (Plan A's preflight), and either side may be kept — it is the same conflict. Documented in `docs/vault.md`; `vault resolve` (phase 5) may later pick automatically.
8. **Track D reads `same_as.yaml` at `head` and rebuilds whole groups** [8]. `_rebuild_in_batches` gains a `groups` argument (it read the working tree's file), and every key `vault sync` rebuilds is first expanded to the whole same-as groups touching it — `projection.rebuild` requires that of its caller, and the old union rebuild did not do it.
9. **Pending counts** [9]: an update folder is a document (Plan B's fingerprint check, now reading `:UserChat` fingerprints too); a curation record counts unless the graph already holds its fingerprint (`:Conflict.record_fingerprint`) — or, for a deletion, while the graph still holds it; every changed same-as group counts (the graph keeps no per-group fingerprint). The staleness line appends ` / K curation records / G same-as groups` only when non-zero, so the existing wording is unchanged otherwise.
10. **Same-as proposals stay machine-local** [10] (spec §14 A6): `sameas propose`/`reject`, `ingest refine-graph` and the adjudicator's same-entity verdicts feed a review queue; an approved group travels as `same_as.yaml`.

## File map

| File | Change |
|---|---|
| `artmind/same_as.py` | `parse_groups(text)` (Task 1) |
| `artmind/table2graph.py` | `_rebuild_in_batches(keys, groups=None)` (Task 1) |
| `artmind/vault_sync.py` | track D (Task 2); track C (Task 5); embed replayed UserChats (Task 7); pending counts (Task 9); docstring (Task 10) |
| `paths.py` | `CURATION_DIR` (Task 3) |
| `artmind/curation_records.py` | new: the record file, `Kind`, `kinds()` (Task 3) |
| `artmind/conflict_records.py` | new: the conflicts kind — record shape and every Cypher statement (Task 3) |
| `artmind/conflicts.py` | `materialize` and `resolve_conflict` write the record and apply it (Task 4) |
| `artmind/ingest.py`, `artmind/sync_state.py`, `artmind/setup.py`, `artmind/graph_snapshot.py` | an update folder commits as a `:UserChat`; fingerprints on `:UserChat`; `:UserChatHistory` (Task 6) |
| `artmind/update.py` | `write_user_chat` stages then commits; `embed_user_chats`; `retract_update` (Tasks 7, 8) |
| `artmind/cli.py` | `update retract`; `vault status` curation phrase; docstrings (Tasks 8, 9, 10) |
| `docs/vault.md`, `artmind/skills/artmind-update/SKILL.md`, `artmind/skills/artmind-curate/SKILL.md`, the spec (§7, §14 A6, new §15 A9) | prose (Task 10) |
| `test/test_curation_records.py`, `test/test_user_chat_commit.py`, `test/test_update_staging.py`, `test/test_update_retract.py` | new |
| `test/test_same_as.py`, `test/test_table2graph.py`, `test/test_vault_sync.py`, `test/test_conflicts.py`, `test/test_conflict_resolution.py`, `test/test_update.py`, `test/test_graph_snapshot.py` | extended; a few existing assertions retargeted where the Cypher they filtered on changed |

No `justfile` change: `update retract` is a new subcommand of an existing group, and no recipe wraps `update` or `vault sync`.

---

### Task 1: `same_as.yaml` from text; `_rebuild_in_batches` takes explicit groups

`vault sync` must read `same_as.yaml` as committed at HEAD (spec §6 A1), never the working tree — but `same_as.load_groups` only reads a path, and `table2graph._rebuild_in_batches`, which the union rebuild uses, calls it itself. This task splits the parsing out (`parse_groups(text)`) and lets the rebuild take its groups. Nothing uses either yet.

**Files:**
- Modify: `artmind/same_as.py`
- Modify: `artmind/table2graph.py`
- Test: `test/test_same_as.py`
- Test: `test/test_table2graph.py`

- [ ] **Step 1: Write the failing tests**

**Replace in** `test/test_same_as.py` (lines 141-143):

```python
    groups = load_groups(path)
    assert len(groups) == 1
    assert groups[0][0] == ("a", "C", "d")
```

**with:**

```python
    groups = load_groups(path)
    assert len(groups) == 1
    assert groups[0][0] == ("a", "C", "d")


# ── parse_groups: the same rules over text (vault sync reads the file from git) ─

_TWO_GROUPS = """
groups:
  - canonical: "a|C|d"
    members:
      - "b|C|d"
      - "a|C|d"
  - canonical: "x|C|d"
    members:
      - "x|C|d"
      - "y|C|e"
"""


def test_parse_groups_reads_text_exactly_as_load_groups_reads_the_file(tmp_path):
    from artmind.same_as import parse_groups

    path = tmp_path / "same_as.yaml"
    path.write_text(_TWO_GROUPS, encoding="utf-8")

    assert parse_groups(_TWO_GROUPS) == load_groups(path) == [
        [_k("a", "C", "d"), _k("b", "C", "d")],
        [_k("x", "C", "d"), _k("y", "C", "e")],
    ]


def test_parse_groups_of_nothing_or_of_garbage_is_no_groups():
    from artmind.same_as import parse_groups

    assert parse_groups(None) == []
    assert parse_groups("") == []
    assert parse_groups("groups: [unclosed") == []
    assert parse_groups("- just\n- a list\n") == []
```

**Replace in** `test/test_table2graph.py` (lines 476-481):

```python
    assert totals["rebuilt"] == 7 and totals["batches"] == 3


# ── CLI ──────────────────────────────────────────────────────────────────────


```

**with:**

```python
    assert totals["rebuilt"] == 7 and totals["batches"] == 3


def test_rebuild_in_batches_uses_the_groups_it_is_given_not_the_file(monkeypatch):
    """`vault sync` passes `same_as.yaml` as committed at head; the file on
    disk may hold an uncommitted edit and must not be read at all."""
    import artmind.graph_query as graph_query
    import artmind.projection as projection
    import artmind.same_as as same_as

    seen: list[dict] = []

    class Session:
        def execute_write(self, fn):
            return fn(object())

    class Ctx:
        def __enter__(self):
            return Session()

        def __exit__(self, *exc):
            return False

    def _no_file():
        raise AssertionError("must not read same_as.yaml from disk")

    monkeypatch.setattr(graph_query, "neo4j_session", lambda: Ctx())
    monkeypatch.setattr(same_as, "load_groups", _no_file)
    monkeypatch.setattr(
        projection, "rebuild",
        lambda tx, keys, **kw: seen.append({"keys": list(keys), "groups": kw["same_as_groups"]}) or {"keys": len(keys)},
    )
    group = [("a", "C", "d"), ("b", "C", "d")]

    t2g._rebuild_in_batches([("a", "C", "d"), ("b", "C", "d"), ("z", "C", "d")], groups=[group])

    assert seen == [{"keys": [("a", "C", "d"), ("b", "C", "d"), ("z", "C", "d")], "groups": [group]}]


# ── CLI ──────────────────────────────────────────────────────────────────────


```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --group dev pytest test/test_same_as.py test/test_table2graph.py -q`
Expected: `3 failed, 51 passed` — the two `parse_groups` tests with `ImportError: cannot import name 'parse_groups'`, and `test_rebuild_in_batches_uses_the_groups_it_is_given_not_the_file` with `TypeError: _rebuild_in_batches() got an unexpected keyword argument 'groups'`.

- [ ] **Step 3: Implement**

**Replace in** `artmind/same_as.py` (lines 96-104):

```python
    if not target.exists():
        return []
    try:
        raw = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    except Exception as e:
        logger.warning("same_as: could not parse {}: {}", target, e)
        return []

    groups: list[list[tuple[str, str, str]]] = []
```

**with:**

```python
    if not target.exists():
        return []
    try:
        text = target.read_text(encoding="utf-8")
    except Exception as e:
        logger.warning("same_as: could not read {}: {}", target, e)
        return []
    return parse_groups(text, source=str(target))


def parse_groups(text: str | None, *, source: str = "same_as.yaml") -> list[list[tuple[str, str, str]]]:
    """`load_groups` over the file's TEXT rather than its path.

    `vault sync` hands this the file as committed at a revision (`git show
    <rev>:.artmind/same_as.yaml`) -- never the working tree (spec 2026-09-26
    §6 A1). Same tolerance as `load_groups`: None, empty, unparseable or
    non-mapping text yields no groups and (for a parse failure) a warning.
    """
    if not text:
        return []
    try:
        raw = yaml.safe_load(text) or {}
    except Exception as e:
        logger.warning("same_as: could not parse {}: {}", source, e)
        return []
    if not isinstance(raw, dict):
        logger.warning("same_as: {} is not a mapping with a 'groups' list; ignoring it", source)
        return []

    groups: list[list[tuple[str, str, str]]] = []
```

**Replace in** `artmind/table2graph.py` (lines 1243-1249):

```python
    return batches


def _rebuild_in_batches(keys: list) -> dict:
    """The projection rebuild for a committed table, one transaction per batch.

    This departs from a document commit, whose rebuild runs inside the same
```

**with:**

```python
    return batches


def _rebuild_in_batches(keys: list, groups: list | None = None) -> dict:
    """The projection rebuild for a committed table, one transaction per batch.

    This departs from a document commit, whose rebuild runs inside the same
```

**Replace in** `artmind/table2graph.py` (lines 1252-1262):

```python
    which part of the projection is stale -- and if a batch fails, it stays
    stale until repaired. Every batch is idempotent, so the repair is to run
    the same `ingest table2graph` again (or `artmind projection rebuild`).
    """
    from artmind import projection, same_as
    from artmind.graph_query import neo4j_session

    groups = same_as.load_groups()
    batches = batch_keys(keys, groups, REBUILD_BATCH)
    totals = {"rebuilt": 0, "deleted": 0, "absent": 0, "keys": 0, "batches": len(batches)}
    with neo4j_session() as session:
```

**with:**

```python
    which part of the projection is stale -- and if a batch fails, it stays
    stale until repaired. Every batch is idempotent, so the repair is to run
    the same `ingest table2graph` again (or `artmind projection rebuild`).

    `groups` are the same-as groups to batch and rebuild with. None reads
    `same_as.yaml` from disk -- right for `ingest table2graph`, wrong for
    `vault sync`, which passes the file as committed at `head` so an
    uncommitted edit never reaches the graph (spec 2026-09-26 §6 A1).
    """
    from artmind import projection, same_as
    from artmind.graph_query import neo4j_session

    groups = same_as.load_groups() if groups is None else groups
    batches = batch_keys(keys, groups, REBUILD_BATCH)
    totals = {"rebuilt": 0, "deleted": 0, "absent": 0, "keys": 0, "batches": len(batches)}
    with neo4j_session() as session:
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --group dev pytest test/test_same_as.py test/test_table2graph.py -q`
Expected: `54 passed`. Then the full suite: `uv run --group dev pytest test/ -q` — all pass.

- [ ] **Step 5: Commit**

```bash
git add artmind/same_as.py artmind/table2graph.py test/test_same_as.py test/test_table2graph.py
git commit -m "feat(same-as): parse groups from text; _rebuild_in_batches takes explicit groups"
```

---

### Task 2: track D — rebuild exactly the members of changed same-as groups, with the committed groups (spec §7)

`classify_diff` gains `plan.same_as_keys` / `plan.same_as_groups`: the aggregate keys of every group added, removed or changed in `.artmind/same_as.yaml` between the bookmark and HEAD (both versions of a changed group), read with `git show` at each revision. A group counts under `--domain` when any member's domain is in scope. An unchanged file costs one `git diff`. `sync()` adds those keys to the union rebuild, expands every key it rebuilds to the whole same-as groups touching it (`projection.affected_keys` set 3, which `projection.rebuild` requires), and rebuilds with the groups **as committed at HEAD** (`same_as_groups_at`). A `same_as.yaml` outside the vault (an explicit `ARTMIND_HOME` elsewhere) is not versioned with it: no track D, and the live file is used — the same rule `_dir_at` applies to mappings.

Existing tests keep passing because the conftest's `SAME_AS_PATH` lies outside every test repo. `_patch_ingest_and_projection`'s rebuild fake gains the `groups` argument and records it.

**Files:**
- Modify: `artmind/vault_sync.py`
- Test: `test/test_vault_sync.py`

- [ ] **Step 1: Write the failing tests**

**Replace in** `test/test_vault_sync.py` (lines 679-685):

```python
    import artmind.ingest as ing
    import artmind.table2graph as t2g

    calls = {"write_to_neo4j": [], "retract_document": [], "rebuild_in_batches": [], "sweeps": []}

    def _fake_write(doc_kg_dir, domain, defer_rebuild=False):
        calls["write_to_neo4j"].append((str(doc_kg_dir), domain))
```

**with:**

```python
    import artmind.ingest as ing
    import artmind.table2graph as t2g

    calls = {"write_to_neo4j": [], "retract_document": [], "rebuild_in_batches": [], "rebuild_groups": [], "sweeps": []}

    def _fake_write(doc_kg_dir, domain, defer_rebuild=False):
        calls["write_to_neo4j"].append((str(doc_kg_dir), domain))
```

**Replace in** `test/test_vault_sync.py` (lines 691-698):

```python
        result = (retract_results or {}).get(doc_id, {"affected_keys": []})
        return {"doc_id": doc_id, "domain": domain, **result}

    def _fake_rebuild_in_batches(keys):
        calls["rebuild_in_batches"].append(sorted(keys))
        return {"rebuilt": len(keys), "deleted": 0, "absent": 0, "keys": len(keys), "batches": 1}

    monkeypatch.setattr(ing, "_write_to_neo4j", _fake_write)
```

**with:**

```python
        result = (retract_results or {}).get(doc_id, {"affected_keys": []})
        return {"doc_id": doc_id, "domain": domain, **result}

    def _fake_rebuild_in_batches(keys, groups=None):
        calls["rebuild_in_batches"].append(sorted(keys))
        calls["rebuild_groups"].append(groups)
        return {"rebuilt": len(keys), "deleted": 0, "absent": 0, "keys": len(keys), "batches": 1}

    monkeypatch.setattr(ing, "_write_to_neo4j", _fake_write)
```

**Replace in** `test/test_vault_sync.py` (lines 2985-2987):

```python
        "artmind: the graph store's sync would fail -- graph unreachable: "
        "Couldn't connect to localhost:7687 -- fix it, then run `artmind vault sync`"
    )
```

**with:**

```python
        "artmind: the graph store's sync would fail -- graph unreachable: "
        "Couldn't connect to localhost:7687 -- fix it, then run `artmind vault sync`"
    )


# ── track D: same_as.yaml edits (spec 2026-09-26 §7) ─────────────────────────


def _patch_same_as(monkeypatch, vault_dir):
    """Point `same_as.SAME_AS_PATH` inside the test vault, where a real
    vault keeps it (`.artmind/same_as.yaml`)."""
    from artmind import same_as

    path = vault_dir / ".artmind" / "same_as.yaml"
    monkeypatch.setattr(same_as, "SAME_AS_PATH", path)
    return path


def _write_groups(path, *groups):
    """`groups` as `(canonical, member, ...)` key strings, canonical first."""
    lines = ["groups:"]
    for group in groups:
        lines.append(f'  - canonical: "{group[0]}"')
        lines.append("    members:")
        lines.extend(f'      - "{member}"' for member in group)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _key(text):
    return tuple(text.split("|"))


_ACME = ("acme|ORG|banking", "acme corp|ORG|banking")
_FCA = ("fca|REGULATOR|banking", "financial conduct authority|REGULATOR|legal")


def _same_as_base(repo, monkeypatch, *groups):
    _patch_kg_dir(monkeypatch, repo)
    _patch_structured_text_dir(monkeypatch, repo)
    path = _patch_same_as(monkeypatch, repo)
    _write_groups(path, *groups)
    (repo / "a.txt").write_text("x")
    _commit_all(repo, "base")
    return path, vs.head_sha(repo)


def test_classify_diff_rebuilds_the_keys_of_an_added_group(repo, monkeypatch):
    path, base = _same_as_base(repo, monkeypatch, _ACME)
    _write_groups(path, _ACME, _FCA)
    _commit_all(repo, "add fca group")

    plan = vs.classify_diff(repo, base, vs.head_sha(repo))

    assert plan.same_as_keys == sorted(_key(k) for k in _FCA)
    assert plan.same_as_groups == 1


def test_classify_diff_rebuilds_the_keys_of_a_removed_group(repo, monkeypatch):
    path, base = _same_as_base(repo, monkeypatch, _ACME, _FCA)
    _write_groups(path, _FCA)
    _commit_all(repo, "drop acme group")

    plan = vs.classify_diff(repo, base, vs.head_sha(repo))

    assert plan.same_as_keys == sorted(_key(k) for k in _ACME)


def test_classify_diff_rebuilds_both_versions_of_a_changed_group(repo, monkeypatch):
    path, base = _same_as_base(repo, monkeypatch, _ACME)
    _write_groups(path, ("acme|ORG|banking", "acme plc|ORG|banking"))
    _commit_all(repo, "acme corp out, acme plc in")

    plan = vs.classify_diff(repo, base, vs.head_sha(repo))

    assert plan.same_as_keys == sorted(_key(k) for k in ("acme|ORG|banking", "acme corp|ORG|banking", "acme plc|ORG|banking"))
    assert plan.same_as_groups == 2, "the old version and the new version"


def test_classify_diff_ignores_reordered_members(repo, monkeypatch):
    path, base = _same_as_base(repo, monkeypatch, ("a|C|d", "b|C|d", "c|C|d"))
    _write_groups(path, ("a|C|d", "c|C|d", "b|C|d"))
    _commit_all(repo, "reorder members")

    plan = vs.classify_diff(repo, base, vs.head_sha(repo))

    assert (plan.same_as_keys, plan.same_as_groups) == ([], 0)


def test_a_new_canonical_is_a_changed_group(repo, monkeypatch):
    path, base = _same_as_base(repo, monkeypatch, _ACME)
    _write_groups(path, (_ACME[1], _ACME[0]))
    _commit_all(repo, "canonical flips")

    plan = vs.classify_diff(repo, base, vs.head_sha(repo))

    assert plan.same_as_keys == sorted(_key(k) for k in _ACME)


def test_classify_diff_does_not_read_same_as_when_it_did_not_change(repo, monkeypatch):
    path, base = _same_as_base(repo, monkeypatch, _ACME)
    (repo / "a.txt").write_text("y")
    _commit_all(repo, "unrelated")
    shows = []
    real_show = vs._show
    monkeypatch.setattr(vs, "_show", lambda *a: shows.append(a) or real_show(*a))

    plan = vs.classify_diff(repo, base, vs.head_sha(repo))

    assert (plan.same_as_keys, plan.same_as_groups) == ([], 0)
    assert shows == []


def test_bootstrap_empty_counts_every_committed_group_as_added(repo, monkeypatch):
    _same_as_base(repo, monkeypatch, _ACME, _FCA)

    plan = vs.classify_diff(repo, vs.EMPTY_TREE_SHA, vs.head_sha(repo))

    assert plan.same_as_keys == sorted(_key(k) for k in _ACME + _FCA)
    assert plan.same_as_groups == 2


def test_classify_diff_reads_same_as_from_git_not_the_working_tree(repo, monkeypatch):
    path, base = _same_as_base(repo, monkeypatch, _ACME)
    _write_groups(path, _ACME, _FCA)
    _commit_all(repo, "add fca")
    _write_groups(path, _ACME, _FCA, ("x|C|d", "y|C|d"))   # uncommitted

    plan = vs.classify_diff(repo, base, vs.head_sha(repo))

    assert plan.same_as_keys == sorted(_key(k) for k in _FCA)


def test_classify_diff_scopes_same_as_groups_by_any_members_domain(repo, monkeypatch):
    path, base = _same_as_base(repo, monkeypatch)
    _write_groups(path, _ACME, _FCA, ("x|C|legal", "y|C|legal"))
    _commit_all(repo, "three groups")

    plan = vs.classify_diff(repo, base, vs.head_sha(repo), domains=["banking"])

    # _FCA spans banking and legal: in scope, and rebuilt whole.
    assert plan.same_as_keys == sorted(_key(k) for k in _ACME + _FCA)
    assert plan.same_as_groups == 2


def test_sync_rebuilds_exactly_the_changed_groups_keys_with_the_committed_groups(repo, monkeypatch):
    path, base = _same_as_base(repo, monkeypatch, _ACME)
    _write_groups(path, _ACME, _FCA)
    _commit_all(repo, "add fca")
    head = vs.head_sha(repo)
    _write_groups(path, ("x|C|d", "y|C|d"))   # an uncommitted edit that must not reach the graph
    from artmind.vault import VaultLayout, write_state
    write_state(VaultLayout(repo), {"last_synced_commit": base})
    calls = _patch_ingest_and_projection(monkeypatch)

    result = vs.sync(repo)

    assert calls["rebuild_in_batches"] == [sorted(_key(k) for k in _FCA)]
    assert calls["rebuild_groups"] == [[[_key(k) for k in _ACME], [_key(k) for k in _FCA]]]
    assert (result["same_as_groups"], result["same_as_keys"]) == (1, 2)
    assert _bookmarks(repo) == (head, head)


def test_sync_rebuilds_a_replayed_documents_whole_group(repo, monkeypatch):
    """A document observing one member of a committed group dirties the whole
    group: `projection.rebuild` folds only the members it is handed."""
    kg_dir = _patch_kg_dir(monkeypatch, repo)
    _patch_structured_text_dir(monkeypatch, repo)
    _write_groups(_patch_same_as(monkeypatch, repo), _ACME)
    _write_doc_folder(kg_dir, "banking", "doc1", "docid-1")
    _commit_all(repo, "group and a doc")
    calls = _patch_ingest_and_projection(monkeypatch, deferred_keys_by_doc={"doc1": [list(_key(_ACME[1]))]})

    vs.sync(repo, bootstrap_empty=True)

    assert calls["rebuild_in_batches"] == [sorted(_key(k) for k in _ACME)]


def test_a_domain_scoped_same_as_sync_does_not_advance_the_bookmarks(repo, monkeypatch):
    path, base = _same_as_base(repo, monkeypatch, _ACME)
    _write_groups(path, _ACME, ("x|C|legal", "y|C|legal"))
    _commit_all(repo, "legal group")
    from artmind.vault import VaultLayout, write_state
    write_state(VaultLayout(repo), {"last_synced_commit": base})
    calls = _patch_ingest_and_projection(monkeypatch)

    result = vs.sync(repo, domains=["legal"])

    assert calls["rebuild_in_batches"] == [[_key("x|C|legal"), _key("y|C|legal")]]
    assert result["cursor_advanced"] is False
    assert _bookmarks(repo) == (None, None)


def test_a_same_as_dry_run_reports_what_the_real_run_rebuilds_and_writes_nothing(repo, monkeypatch):
    path, base = _same_as_base(repo, monkeypatch, _ACME)
    _write_groups(path, _ACME, _FCA)
    _commit_all(repo, "add fca")
    from artmind.vault import VaultLayout, read_state, write_state
    write_state(VaultLayout(repo), {"last_synced_commit": base})
    calls = _patch_ingest_and_projection(monkeypatch)

    dry = vs.sync(repo, dry_run=True)

    assert (dry["same_as_groups"], dry["same_as_keys"]) == (1, 2)
    assert calls["rebuild_in_batches"] == []
    assert read_state(VaultLayout(repo)) == {"last_synced_commit": base}
    real = vs.sync(repo)
    assert (real["same_as_groups"], real["same_as_keys"]) == (dry["same_as_groups"], dry["same_as_keys"])
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --group dev pytest test/test_vault_sync.py -q`
Expected: `13 failed, 135 passed` — the nine classifier tests with `AttributeError: 'SyncPlan' object has no attribute 'same_as_keys'`; the `sync()` tests with `AssertionError` on the recorded rebuild (nothing, or only the replayed key, was rebuilt) and, for the dry-run test, `KeyError: 'same_as_groups'`.

- [ ] **Step 3: Implement**

**Replace in** `artmind/vault_sync.py` (lines 255-260):

```python
    replay_docs: list[tuple[str, str]] = field(default_factory=list)        # (domain, docdir)
    retract: list[tuple[str, str]] = field(default_factory=list)            # (domain, doc_id)
    regenerate_tables: list[tuple[str, str]] = field(default_factory=list)  # (domain, table_name)


def _in_scope(domain: str, domains: list[str] | None) -> bool:
```

**with:**

```python
    replay_docs: list[tuple[str, str]] = field(default_factory=list)        # (domain, docdir)
    retract: list[tuple[str, str]] = field(default_factory=list)            # (domain, doc_id)
    regenerate_tables: list[tuple[str, str]] = field(default_factory=list)  # (domain, table_name)
    same_as_keys: list[tuple[str, str, str]] = field(default_factory=list)  # track D: keys to rebuild
    same_as_groups: int = 0                                                 # track D: groups added/removed/changed


def _in_scope(domain: str, domains: list[str] | None) -> bool:
```

**Replace in** `artmind/vault_sync.py` (lines 729-734):

```python
    return regenerate, retract


def classify_diff(
    vault_dir: Path, base: str, head: str, domains: list[str] | None = None
) -> SyncPlan:
```

**with:**

```python
    return regenerate, retract


# ── track D: same_as.yaml (spec 2026-09-26 §7) ────────────────────────────────


def _same_as_relpath(vault_dir: Path) -> str | None:
    """`same_as.yaml`'s path relative to the vault, or None when it lives
    outside it (a run folder named by an explicit `ARTMIND_HOME`): then it is
    not versioned with the vault, and there is nothing to diff."""
    from artmind import same_as

    try:
        return str(Path(same_as.SAME_AS_PATH).relative_to(vault_dir))
    except ValueError:
        return None


def same_as_groups_at(vault_dir: Path, rev: str) -> list[list[tuple[str, str, str]]]:
    """The same-as groups as committed at `rev` -- never the working tree,
    where an uncommitted edit may sit (spec 2026-09-26 §6 A1). The empty tree
    (`--bootstrapEmpty`'s base) has none. A `same_as.yaml` outside the vault
    is not versioned with it, so its live copy is all there is."""
    from artmind import same_as

    rel = _same_as_relpath(vault_dir)
    if rel is None:
        return same_as.load_groups()
    if rev == EMPTY_TREE_SHA:
        return []
    return same_as.parse_groups(_show(vault_dir, rev, rel), source=f"{rel} at {rev[:12]}")


def _group_identity(group: list[tuple[str, str, str]]) -> tuple:
    """A group as a comparable value: its canonical and its member set. Member
    order in the file carries no meaning; which member is canonical does."""
    return (group[0], tuple(sorted(group)))


def _classify_same_as(
    vault_dir: Path, base: str, head: str, domains: list[str] | None
) -> tuple[list[tuple[str, str, str]], int]:
    """Track D: `(keys, groups)` -- every aggregate key in a same-as group
    added, removed or changed between `base` and `head`, and how many such
    groups there were. A removed group's members must be rebuilt as separate
    entities again; an added group's, folded (or linked); a changed group's,
    both, so the keys of its `base` AND `head` versions are included.

    Scoped like the other tracks: a group counts when any member's domain is
    in scope (then all its members are rebuilt -- a group is rebuilt whole).
    Reads `same_as.yaml` at both revisions from git; an unchanged file costs
    one `git diff` and nothing else."""
    rel = _same_as_relpath(vault_dir)
    if rel is None:
        return [], 0
    if not _diff_name_status(vault_dir, base, head, vault_dir / rel):
        return [], 0
    before = {_group_identity(g) for g in same_as_groups_at(vault_dir, base)}
    after = {_group_identity(g) for g in same_as_groups_at(vault_dir, head)}
    changed = [
        members for _, members in sorted(before ^ after)
        if any(_in_scope(key[2], domains) for key in members)
    ]
    keys = sorted({key for members in changed for key in members})
    return keys, len(changed)


def classify_diff(
    vault_dir: Path, base: str, head: str, domains: list[str] | None = None
) -> SyncPlan:
```

**Replace in** `artmind/vault_sync.py` (lines 753-758):

```python
        if (domain, doc_id) not in seen:
            seen.add((domain, doc_id))
            plan.retract.append((domain, doc_id))
    return plan


```

**with:**

```python
        if (domain, doc_id) not in seen:
            seen.add((domain, doc_id))
            plan.retract.append((domain, doc_id))
    plan.same_as_keys, plan.same_as_groups = _classify_same_as(vault_dir, base, head, domains)
    return plan


```

**Replace in** `artmind/vault_sync.py` (lines 1049-1054):

```python
            "retract": len(plan.retract),
            "regenerate_tables": len(restore_tables),
            "structured_only_tables": structured_only_dry,
            "cursor_would_advance": not domains,
        }

```

**with:**

```python
            "retract": len(plan.retract),
            "regenerate_tables": len(restore_tables),
            "structured_only_tables": structured_only_dry,
            "same_as_groups": plan.same_as_groups,
            "same_as_keys": len(plan.same_as_keys),
            "cursor_would_advance": not domains,
        }

```

**Replace in** `artmind/vault_sync.py` (lines 1128-1136):

```python
        result = ingest.retract_document(doc_id, domain)
        all_keys.update(tuple(k) for k in result.get("affected_keys") or [])

    # ── the union rebuild (§5 step 4), chunked exactly like table2graph's own ─
    if all_keys:
        projection_summary = _rebuild_in_batches(sorted(all_keys))
    else:
        projection_summary = {"rebuilt": 0, "deleted": 0, "absent": 0, "keys": 0, "batches": 0}

```

**with:**

```python
        result = ingest.retract_document(doc_id, domain)
        all_keys.update(tuple(k) for k in result.get("affected_keys") or [])

    # ── track D: every key of a same-as group added/removed/changed ─────────
    all_keys.update(tuple(k) for k in plan.same_as_keys)

    # ── the union rebuild (§5 step 4), chunked exactly like table2graph's own ─
    #    With the same-as groups as committed at `head`, never the working
    #    tree's `same_as.yaml`; and every group touching a key is rebuilt
    #    whole (`projection.affected_keys`' set 3), which `projection.rebuild`
    #    requires of its caller.
    if all_keys:
        from artmind import projection

        head_groups = same_as_groups_at(vault_dir, head)
        all_keys = projection.affected_keys(incoming=sorted(all_keys), same_as_groups=head_groups)
        projection_summary = _rebuild_in_batches(sorted(all_keys), groups=head_groups)
    else:
        projection_summary = {"rebuilt": 0, "deleted": 0, "absent": 0, "keys": 0, "batches": 0}

```

**Replace in** `artmind/vault_sync.py` (lines 1166-1171):

```python
        "retracted": len(plan.retract),
        "regenerated_tables": len(restore_tables),
        "structured_only_tables": structured_only,
        "projection": projection_summary,
        "domains_swept": touched_domains,
    }
```

**with:**

```python
        "retracted": len(plan.retract),
        "regenerated_tables": len(restore_tables),
        "structured_only_tables": structured_only,
        "same_as_groups": plan.same_as_groups,
        "same_as_keys": len(plan.same_as_keys),
        "projection": projection_summary,
        "domains_swept": touched_domains,
    }
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --group dev pytest test/test_vault_sync.py -q`
Expected: `148 passed`. Then the full suite — all pass.

- [ ] **Step 5: Commit**

```bash
git add artmind/vault_sync.py test/test_vault_sync.py
git commit -m "feat(vault-sync): track D -- rebuild the keys of same-as groups changed between base and head, with the committed groups"
```

---

### Task 3: curation records — one vault file per record; the conflicts kind (spec §14 A6)

Two new modules, used by nothing yet.

`artmind/curation_records.py` owns the file: `.artmind/data/curation/<kind>/<id>.json` (`paths.CURATION_DIR`), a deterministic serialisation (sorted keys, two-space indent, one trailing newline), its fingerprint (sha256 of the bytes), an atomic write that leaves unchanged bytes alone, and `Kind` — how one kind of record reaches the graph (`apply`, `remove`, `read_fingerprints`, `domains`, `phase`). `kinds()` is the registry `vault sync` iterates.

`artmind/conflict_records.py` is the first kind. A record holds the conflict (id, verdict, aspect, both claims, severity, entity class), both entities (id, key, name, domain, side), its domains, its evidence (chunk and document ids), status, resolution reason, timestamps and model. `apply` MERGEs the `:Conflict` node and SETs **every** field from the record (no `ON CREATE`: the record is the truth), plus `CONFLICT_OF`/`CONFLICTS_WITH` when both entities exist and `EVIDENCE` to the chunks that exist, and records `record_fingerprint`. `remove` deletes the `CONFLICTS_WITH` edges and the adjudicator node only — never a projection conflict. `record_from_graph` rebuilds the record of a conflict detected before records existed.

**Files:**
- Create: `artmind/conflict_records.py`
- Create: `artmind/curation_records.py`
- Modify: `paths.py`
- Test: `test/test_curation_records.py` (new)

- [ ] **Step 1: Write the failing tests**

**Create** `test/test_curation_records.py`:

```python
"""Curation records -- graph-only writes made to travel as one vault file per
record (spec 2026-09-26 §7, §14 A6) -- and the first kind, conflicts.

No Neo4j: a recording transaction stands in, and every graph test asserts on
the Cypher and the parameters actually sent (CLAUDE.md §3)."""
import hashlib
import json
from contextlib import contextmanager

import pytest

from artmind import conflict_records, curation_records


@pytest.fixture(autouse=True)
def curation_dir(tmp_path, monkeypatch):
    import paths

    target = tmp_path / ".artmind" / "data" / "curation"
    monkeypatch.setattr(paths, "CURATION_DIR", target)
    return target


class _Result:
    def __init__(self, rows=()):
        self._rows = list(rows)

    def single(self):
        return self._rows[0] if self._rows else None

    def data(self):
        return list(self._rows)


class _Tx:
    def __init__(self, answers=None):
        self.calls = []
        self._answers = answers or {}

    def run(self, cypher, **params):
        self.calls.append((cypher, params))
        for marker, rows in self._answers.items():
            if marker in cypher:
                return _Result(rows)
        return _Result()


def _record(**overrides):
    record = conflict_records.record_from_detection(
        "c0ffee",
        {
            "id_a": "ea", "name_a": "Fee reversal", "domain_a": "banking.ops", "key_a": "fee reversal|POLICY|banking.ops",
            "id_b": "eb", "name_b": "Fee reversals", "domain_b": "banking.risk", "key_b": "fee reversals|POLICY|banking.risk",
            "entity_class": "POLICY",
        },
        {"verdict": "conflicting_claims", "aspect": "approval limit", "claim_a": "CEO", "claim_b": "Manager", "severity": "high"},
        [{"id": "d1_001", "doc_id": "d1", "text": "..."}],
        [{"id": "d2_004", "doc_id": "d2", "text": "..."}],
        "model-x",
        "2026-09-28T10:00:00+00:00",
    )
    record.update(overrides)
    return record


# ── the file ────────────────────────────────────────────────────────────────


def test_a_record_serialises_deterministically():
    data = curation_records.serialize({"b": 1, "a": {"y": 2, "x": 3}})

    assert data == b'{\n  "a": {\n    "x": 3,\n    "y": 2\n  },\n  "b": 1\n}\n'
    assert curation_records.fingerprint(data) == hashlib.sha256(data).hexdigest()


def test_write_record_writes_one_file_per_record_and_returns_its_fingerprint(curation_dir):
    record = _record()

    fp = curation_records.write_record("conflicts", record)

    path = curation_dir / "conflicts" / "c0ffee.json"
    assert path.read_bytes() == curation_records.serialize(record)
    assert fp == curation_records.fingerprint(path.read_bytes())
    assert [p.name for p in (curation_dir / "conflicts").iterdir()] == ["c0ffee.json"], "no temp file left"
    assert curation_records.read_record("conflicts", "c0ffee") == record


def test_an_unchanged_record_is_not_rewritten(curation_dir):
    record = _record()
    curation_records.write_record("conflicts", record)
    path = curation_dir / "conflicts" / "c0ffee.json"
    before = path.stat().st_ino, path.stat().st_mtime_ns

    curation_records.write_record("conflicts", dict(record))

    assert (path.stat().st_ino, path.stat().st_mtime_ns) == before


@pytest.mark.parametrize("bad", ["../escape", "a/b", ".hidden", "", "x y"])
def test_an_unsafe_record_id_is_refused(bad):
    with pytest.raises(ValueError, match="safe curation record id"):
        curation_records.record_path("conflicts", bad)


def test_delete_record_removes_the_file(curation_dir):
    curation_records.write_record("conflicts", _record())

    assert curation_records.delete_record("conflicts", "c0ffee") is True
    assert curation_records.read_record("conflicts", "c0ffee") is None
    assert curation_records.delete_record("conflicts", "c0ffee") is False


def test_parse_tolerates_garbage():
    assert curation_records.parse(None) is None
    assert curation_records.parse(b"not json") is None
    assert curation_records.parse(b"[1, 2]") is None
    assert curation_records.parse(b'{"id": "x"}') == {"id": "x"}


def test_the_conflicts_kind_is_registered_to_run_after_the_rebuild():
    kind = curation_records.kinds()["conflicts"]

    assert kind.phase == "post", "CONFLICTS_WITH joins two rebuilt :Entity nodes"
    assert kind.domains(_record()) == ["banking.ops", "banking.risk"]


# ── the conflict record ─────────────────────────────────────────────────────


def test_a_detected_conflict_becomes_an_open_record_with_both_sides():
    record = _record()

    assert record["status"] == "open"
    assert record["entities"] == [
        {"side": "a", "id": "ea", "key": "fee reversal|POLICY|banking.ops", "name": "Fee reversal", "domain": "banking.ops"},
        {"side": "b", "id": "eb", "key": "fee reversals|POLICY|banking.risk", "name": "Fee reversals", "domain": "banking.risk"},
    ]
    assert record["evidence"] == [
        {"side": "a", "chunk_id": "d1_001", "doc_id": "d1"},
        {"side": "b", "chunk_id": "d2_004", "doc_id": "d2"},
    ]
    assert (record["aspect"], record["claim_a"], record["claim_b"], record["severity"]) == (
        "approval limit", "CEO", "Manager", "high",
    )
    json.dumps(record)  # plain JSON, nothing a file cannot hold


def test_apply_sets_every_field_from_the_record_and_joins_both_entities():
    tx = _Tx()
    record = _record(status="resolved", resolution_reason="v3 settled it", resolved_at="2026-09-28T11:00:00+00:00")

    keys = conflict_records.apply(tx, record, "fp-1")

    assert keys == set()
    (cypher, params), (ev_cypher, ev_params) = tx.calls
    assert cypher.strip().startswith("MERGE (co:Conflict {id: $id})")
    assert "ON CREATE" not in cypher, "the record is the truth: every field is SET"
    assert "MERGE (a)-[ra:CONFLICTS_WITH]->(b)" in cypher and "MERGE (co)-[:CONFLICT_OF]->(a)" in cypher
    assert params == {
        "id": "c0ffee", "verdict": "conflicting_claims", "aspect": "approval limit",
        "claim_a": "CEO", "claim_b": "Manager", "severity": "high", "entity_class": "POLICY",
        "domains": ["banking.ops", "banking.risk"], "status": "resolved",
        "resolution_reason": "v3 settled it", "resolved_at": "2026-09-28T11:00:00+00:00",
        "detected_at": "2026-09-28T10:00:00+00:00", "detected_by_model": "model-x",
        "fingerprint": "fp-1", "idA": "ea", "idB": "eb",
    }
    assert "MERGE (co)-[:EVIDENCE {side: ev.side}]->(c)" in ev_cypher
    assert ev_params == {"id": "c0ffee", "evidence": [
        {"side": "a", "chunk_id": "d1_001"}, {"side": "b", "chunk_id": "d2_004"},
    ]}


def test_apply_without_evidence_sends_no_evidence_statement():
    tx = _Tx()

    conflict_records.apply(tx, _record(evidence=[]), "fp")

    assert len(tx.calls) == 1


def test_remove_deletes_the_edges_and_only_an_adjudicator_node():
    tx = _Tx()

    conflict_records.remove(tx, {"id": "c0ffee"})

    assert tx.calls == [
        ("MATCH (:Entity)-[r:CONFLICTS_WITH {conflict_id: $id}]->(:Entity) DELETE r", {"id": "c0ffee"}),
        ("MATCH (co:Conflict {id: $id, _source: 'adjudicator'}) DETACH DELETE co", {"id": "c0ffee"}),
    ]


def test_read_fingerprints_is_one_query(monkeypatch):
    import artmind.graph_query as graph_query

    session = _Tx(answers={"record_fingerprint": [{"id": "a", "fingerprint": "f"}, {"id": "b", "fingerprint": None}]})
    timeouts = []

    @contextmanager
    def _fake(access_mode=None, *, timeout=None):
        timeouts.append(timeout)
        yield session

    monkeypatch.setattr(graph_query, "neo4j_session", _fake)

    assert conflict_records.read_fingerprints(["b", "a", "a"], timeout=2.0) == {"a": "f", "b": None}
    assert session.calls[0][1] == {"ids": ["a", "b"]}
    assert timeouts == [2.0]
    assert conflict_records.read_fingerprints([]) == {}
    assert len(session.calls) == 1


def test_record_from_graph_rebuilds_a_pre_file_conflicts_record():
    tx = _Tx(answers={"properties(co) AS co": [{
        "co": {"id": "c0ffee", "aspect": "limit", "claim_a": "A", "claim_b": "B", "severity": "high",
               "status": "resolved", "resolution_reason": "done", "resolved_at": "t1",
               "detected_at": "t0", "detected_by_model": "m", "domains": ["x", "y"], "_source": "adjudicator"},
        "entities": [
            {"id": "eb", "key": "b|C|y", "name": "B", "domain": "y"},
            {"id": "ea", "key": "a|C|x", "name": "A", "domain": "x"},
        ],
        "evidence": [{"side": "b", "chunk_id": "c2", "doc_id": "d2"}, {"side": None, "chunk_id": None, "doc_id": None}],
    }]})

    record = conflict_records.record_from_graph(tx, "c0ffee")

    assert record["status"] == "resolved" and record["resolution_reason"] == "done"
    assert [e["id"] for e in record["entities"]] == ["ea", "eb"]
    assert [e["side"] for e in record["entities"]] == ["a", "b"]
    assert record["evidence"] == [{"side": "b", "chunk_id": "c2", "doc_id": "d2"}]
    assert record["domains"] == ["x", "y"]
    assert tx.calls[0][1] == {"id": "c0ffee"}


def test_record_from_graph_is_none_without_a_node():
    assert conflict_records.record_from_graph(_Tx(), "nope") is None
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --group dev pytest test/test_curation_records.py -q`
Expected: `1 error` during collection — `ImportError: cannot import name 'conflict_records' from 'artmind'`.

- [ ] **Step 3: Implement**

**Create** `artmind/conflict_records.py`:

```python
"""Conflict records -- the `conflicts` curation kind (spec 2026-09-26 §14 A6).

A cross-domain conflict is detected by an LLM (`conflicts.materialize`) and
closed by a human (`conflicts.resolve_conflict`). Both used to live only in
the graph, so a machine with its own Neo4j never saw either. Each conflict is
now also a file, `.artmind/data/curation/conflicts/<id>.json`, and this module
owns the record's shape and every Cypher statement that writes it to (or
removes it from) the graph -- the one path shared by detection, resolution
and `vault sync`.

A record:

    {"id", "verdict", "aspect", "claim_a", "claim_b", "severity",
     "entity_class", "entities": [{"side", "id", "key", "name", "domain"}, x2],
     "domains", "evidence": [{"side", "chunk_id", "doc_id"}],
     "status", "resolution_reason", "resolved_at",
     "detected_at", "detected_by_model", "source": "adjudicator"}

The id is `conflicts.conflict_id` -- sha1 of the two entity ids (sorted) and
the aspect -- and entity ids are sha256 of `name|class|domain`, so two
machines detecting the same conflict name the same record.

Only the adjudicator's conflicts are records. The projection's own
`:Conflict {_source: 'projection'}` nodes are derived from observations and
rebuilt by every projection rebuild; they need no file.
"""
from __future__ import annotations

from artmind.curation_records import Kind

NAME = "conflicts"

_APPLY = """
MERGE (co:Conflict {id: $id})
SET co._source = 'adjudicator',
    co.verdict = $verdict, co.aspect = $aspect,
    co.claim_a = $claim_a, co.claim_b = $claim_b, co.severity = $severity,
    co.entity_class = $entity_class, co.domains = $domains,
    co.status = $status, co.resolution_reason = $resolution_reason, co.resolved_at = $resolved_at,
    co.detected_at = $detected_at, co.detected_by_model = $detected_by_model,
    co.record_fingerprint = $fingerprint
WITH co
MATCH (a:Entity {_id: $idA}), (b:Entity {_id: $idB})
MERGE (co)-[:CONFLICT_OF]->(a)
MERGE (co)-[:CONFLICT_OF]->(b)
MERGE (a)-[ra:CONFLICTS_WITH]->(b) SET ra.conflict_id = $id, ra.aspect = $aspect
MERGE (b)-[rb:CONFLICTS_WITH]->(a) SET rb.conflict_id = $id, rb.aspect = $aspect
"""

_APPLY_EVIDENCE = """
UNWIND $evidence AS ev
MATCH (co:Conflict {id: $id}), (c:DocChunk {id: ev.chunk_id})
MERGE (co)-[:EVIDENCE {side: ev.side}]->(c)
"""

_REMOVE_EDGES = "MATCH (:Entity)-[r:CONFLICTS_WITH {conflict_id: $id}]->(:Entity) DELETE r"
_REMOVE_NODE = "MATCH (co:Conflict {id: $id, _source: 'adjudicator'}) DETACH DELETE co"

_READ_FINGERPRINTS = (
    "MATCH (co:Conflict) WHERE co.id IN $ids "
    "RETURN co.id AS id, co.record_fingerprint AS fingerprint"
)

_FROM_GRAPH = """
MATCH (co:Conflict {id: $id})
WHERE coalesce(co._source, 'adjudicator') = 'adjudicator'
OPTIONAL MATCH (co)-[:CONFLICT_OF]->(e:Entity)
WITH co, collect(DISTINCT {id: e._id, key: e.key, name: e.name, domain: e._domain}) AS entities
OPTIONAL MATCH (co)-[ev:EVIDENCE]->(c:DocChunk)
RETURN properties(co) AS co, entities,
       collect(DISTINCT {side: ev.side, chunk_id: c.id, doc_id: c.doc_id}) AS evidence
"""


def record_from_detection(
    conflict_id: str, pair: dict, verdict: dict, evidence_a: list[dict], evidence_b: list[dict],
    model: str, detected_at: str,
) -> dict:
    """A fresh record for a `conflicting_claims` verdict, status open."""
    return {
        "id": conflict_id,
        "verdict": verdict["verdict"],
        "aspect": verdict.get("aspect") or "",
        "claim_a": verdict.get("claim_a") or "",
        "claim_b": verdict.get("claim_b") or "",
        "severity": verdict.get("severity") or "low",
        "entity_class": pair.get("entity_class"),
        "entities": [
            {"side": side, "id": pair[f"id_{side}"], "key": pair.get(f"key_{side}"),
             "name": pair.get(f"name_{side}"), "domain": pair.get(f"domain_{side}")}
            for side in ("a", "b")
        ],
        "domains": sorted({pair["domain_a"], pair["domain_b"]}),
        "evidence": [
            {"side": side, "chunk_id": c["id"], "doc_id": c.get("doc_id")}
            for side, chunks in (("a", evidence_a), ("b", evidence_b)) for c in chunks
        ],
        "status": "open",
        "resolution_reason": None,
        "resolved_at": None,
        "detected_at": detected_at,
        "detected_by_model": model,
        "source": "adjudicator",
    }


def record_from_graph(session, conflict_id: str) -> dict | None:
    """The record an adjudicator `:Conflict` node that predates its file
    would have had -- read back from the graph, so resolving (or re-detecting)
    an old conflict writes its file for the first time. None when there is no
    such node. The graph does not know which entity was side `a`, so sides
    follow entity-id order."""
    rec = session.run(_FROM_GRAPH, id=conflict_id).single()
    node = rec.get("co") if rec else None
    if not node:
        return None
    entities = sorted((e for e in rec.get("entities") or [] if e.get("id")), key=lambda e: e["id"])
    return {
        "id": conflict_id,
        "verdict": node.get("verdict") or "conflicting_claims",
        "aspect": node.get("aspect") or "",
        "claim_a": node.get("claim_a") or "",
        "claim_b": node.get("claim_b") or "",
        "severity": node.get("severity") or "low",
        "entity_class": node.get("entity_class"),
        "entities": [
            {"side": side, "id": e["id"], "key": e.get("key"), "name": e.get("name"), "domain": e.get("domain")}
            for side, e in zip(("a", "b"), entities)
        ],
        "domains": sorted(node.get("domains") or {e.get("domain") for e in entities if e.get("domain")}),
        "evidence": sorted(
            ({"side": ev.get("side"), "chunk_id": ev["chunk_id"], "doc_id": ev.get("doc_id")}
             for ev in rec.get("evidence") or [] if ev.get("chunk_id")),
            key=lambda ev: (ev["side"] or "", ev["chunk_id"]),
        ),
        "status": node.get("status") or "open",
        "resolution_reason": node.get("resolution_reason"),
        "resolved_at": node.get("resolved_at"),
        "detected_at": node.get("detected_at"),
        "detected_by_model": node.get("detected_by_model"),
        "source": "adjudicator",
    }


def apply(tx, record: dict, fingerprint: str) -> set:
    """MERGE the `:Conflict` node from `record` -- every field SET, so the
    record, not whatever the graph held, is the truth -- plus its
    `CONFLICT_OF`/`CONFLICTS_WITH` edges (when both entities exist) and its
    `EVIDENCE` edges (to the chunks that exist). Idempotent. Changes no
    projection, so returns no keys."""
    entities = record.get("entities") or []
    id_a = entities[0]["id"] if len(entities) > 0 else None
    id_b = entities[1]["id"] if len(entities) > 1 else None
    tx.run(
        _APPLY,
        id=record["id"], verdict=record.get("verdict"), aspect=record.get("aspect"),
        claim_a=record.get("claim_a"), claim_b=record.get("claim_b"), severity=record.get("severity"),
        entity_class=record.get("entity_class"), domains=list(record.get("domains") or []),
        status=record.get("status") or "open", resolution_reason=record.get("resolution_reason"),
        resolved_at=record.get("resolved_at"), detected_at=record.get("detected_at"),
        detected_by_model=record.get("detected_by_model"), fingerprint=fingerprint,
        idA=id_a, idB=id_b,
    )
    evidence = [
        {"side": ev.get("side"), "chunk_id": ev["chunk_id"]}
        for ev in record.get("evidence") or [] if ev.get("chunk_id")
    ]
    if evidence:
        tx.run(_APPLY_EVIDENCE, id=record["id"], evidence=evidence)
    return set()


def remove(tx, record: dict) -> set:
    """Undo a record whose file was deleted: its `CONFLICTS_WITH` edges and
    the adjudicator `:Conflict` node (with its `CONFLICT_OF`/`EVIDENCE`
    edges). A projection conflict is never touched."""
    tx.run(_REMOVE_EDGES, id=record["id"])
    tx.run(_REMOVE_NODE, id=record["id"])
    return set()


def read_fingerprints(ids: list[str], *, timeout: float | None = None) -> dict:
    """`{id: record_fingerprint}` for every id the graph holds a `:Conflict`
    for, in ONE query. A conflict detected before records existed maps to
    None."""
    from artmind import graph_query

    if not ids:
        return {}
    with graph_query.neo4j_session(timeout=timeout) as session:
        rows = session.run(_READ_FINGERPRINTS, ids=sorted(set(ids))).data()
    return {row["id"]: row["fingerprint"] for row in rows}


def domains(record: dict) -> list:
    return list(record.get("domains") or [])


KIND = Kind(name=NAME, phase="post", apply=apply, remove=remove, read_fingerprints=read_fingerprints, domains=domains)
```

**Create** `artmind/curation_records.py`:

```python
"""Curation records: graph-only writes made to travel as vault files (spec
2026-09-26 §7, D6 -- the graph must be rebuildable from the vault alone).

A machine with its own Neo4j never sees a write that lives only in another
machine's graph. So every curation write (a detected conflict and its
resolution, in this module's first kind) is also a file:

    .artmind/data/curation/<kind>/<record id>.json

**One file per record, never one shared file.** Every path under
`.artmind/data/**` is `merge=binary` (spec R3), so a single `conflicts.yaml`
would turn any two machines' independent curation into a whole-file git
conflict that blocks `vault sync` -- the same lesson as the per-table
`.meta.json` that replaced `structured_text/manifest.json` (spec R5). With
one file per record, two machines only collide when they write the SAME
record.

A **kind** says how its records reach the graph (`Kind`); `vault sync`'s
curation track applies every added or changed record and removes every
deleted one, for every kind in `kinds()`. Files are written atomically (a
sibling temp file, then `os.replace`) so an Obsidian Git auto-commit never
captures half a record, and serialised deterministically (sorted keys) so an
unchanged record is byte-identical and never churns in git.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

#: The temp-file suffix `write_record` renames from. Shares
#: `atomic_dir.TMP_SUFFIX` so a leftover is recognisably artmind scratch; the
#: curation track reads only `*.json`, so a leftover is never applied.
TMP_SUFFIX = ".artmind-tmp"

_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


@dataclass(frozen=True)
class Kind:
    """How one kind of curation record reaches the graph.

    - `name`: its directory under `curation/`.
    - `phase`: `"pre"` -- applied before `vault sync`'s union projection
      rebuild, and the keys it returns join that rebuild -- or `"post"`,
      applied after it, because it needs the rebuilt entities (a conflict's
      `CONFLICTS_WITH` edges join two `:Entity` nodes).
    - `apply(tx, record, fingerprint)`: write one record, idempotently
      (MERGE), recording `fingerprint` on the graph; returns the aggregate
      keys whose projection it changed.
    - `remove(tx, record)`: undo one record whose file was deleted --
      `record` is the file as it last existed (at `vault sync`'s base), or
      just `{"id": ...}` when that cannot be read; returns keys the same way.
    - `read_fingerprints(ids, *, timeout)`: `{id: fingerprint}` for the ids
      the graph holds, in one query -- what lets a shared graph skip records
      another machine already applied, and `vault status` count only what is
      really pending.
    - `domains(record)`: the domains a record belongs to, for `--domain`.
    """

    name: str
    phase: str
    apply: Callable[[Any, dict, str], set]
    remove: Callable[[Any, dict], set]
    read_fingerprints: Callable[..., dict]
    domains: Callable[[dict], list]


def kinds() -> dict[str, Kind]:
    """Every registered kind, by directory name, in apply order."""
    from artmind import conflict_records

    return {kind.name: kind for kind in (conflict_records.KIND,)}


def curation_dir() -> Path:
    """`paths.CURATION_DIR`, resolved at call time so tests can repoint it."""
    import paths

    return Path(paths.CURATION_DIR)


def _check_id(record_id: str) -> str:
    if not isinstance(record_id, str) or not _SAFE_ID.fullmatch(record_id):
        raise ValueError(f"{record_id!r} is not a safe curation record id (letters, digits, '.', '_', '-')")
    return record_id


def record_path(kind: str, record_id: str) -> Path:
    return curation_dir() / kind / f"{_check_id(record_id)}.json"


def serialize(record: dict) -> bytes:
    """The one byte form of a record: sorted keys, two-space indent, UTF-8,
    one trailing newline. Deterministic, so rewriting an unchanged record
    changes nothing git can see."""
    return (json.dumps(record, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")


def fingerprint(data: bytes) -> str:
    """sha256 of a record file's bytes -- the working-tree file's when this
    machine writes it, the committed blob's when `vault sync` applies it."""
    return hashlib.sha256(data).hexdigest()


def write_record(kind: str, record: dict) -> str:
    """Write `record` to its file atomically and return its fingerprint.
    Bytes already on disk are left alone (no mtime churn for Obsidian Git)."""
    data = serialize(record)
    path = record_path(kind, record["id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file() and path.read_bytes() == data:
        return fingerprint(data)
    tmp = path.with_name(path.name + TMP_SUFFIX)
    try:
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)
    return fingerprint(data)


def read_record(kind: str, record_id: str) -> dict | None:
    """The record as it stands in this working tree, or None."""
    path = record_path(kind, record_id)
    if not path.is_file():
        return None
    return parse(path.read_bytes())


def parse(data: bytes | None) -> dict | None:
    """A record from its bytes; None when absent or not a JSON object."""
    if data is None:
        return None
    try:
        obj = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return obj if isinstance(obj, dict) else None


def delete_record(kind: str, record_id: str) -> bool:
    """Remove a record's file (a plain delete: Obsidian Git records it).
    Returns whether there was one."""
    path = record_path(kind, record_id)
    if not path.is_file():
        return False
    path.unlink()
    return True
```

**Replace in** `paths.py` (lines 205-207):

```python
STRUCTURED_DIR = DATA_DIR / "structured"   # DuckDB catalog + <domain>/<table>.parquet
STRUCTURED_SNAPSHOT_DIR = DATA_DIR / "structured_snapshot"   # db backup/restore .tar.gz files
STRUCTURED_TEXT_DIR = DATA_DIR / "structured_text"   # db export-text/reindex CSV + manifest.json
```

**with:**

```python
STRUCTURED_DIR = DATA_DIR / "structured"   # DuckDB catalog + <domain>/<table>.parquet
STRUCTURED_SNAPSHOT_DIR = DATA_DIR / "structured_snapshot"   # db backup/restore .tar.gz files
STRUCTURED_TEXT_DIR = DATA_DIR / "structured_text"   # db export-text/reindex CSV + manifest.json
# Graph-only curation made to travel (spec 2026-09-26 §7, D6): one JSON file
# per record, `curation/<kind>/<id>.json` -- committed, so a machine with its
# own Neo4j rebuilds the same curation from the vault (`artmind.curation_records`).
CURATION_DIR = DATA_DIR / "curation"
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --group dev pytest test/test_curation_records.py -q`
Expected: `18 passed`. Then the full suite — all pass.

- [ ] **Step 5: Commit**

```bash
git add artmind/conflict_records.py artmind/curation_records.py paths.py test/test_curation_records.py
git commit -m "feat(curation): one vault file per curation record; the conflicts kind and its Cypher"
```

---

### Task 4: conflict detection and resolution write the record, and the graph is written from it

`conflicts.materialize` (a `conflicting_claims` verdict) and `conflicts.resolve_conflict` now go vault-first: write the record, then `conflict_records.apply` it — the same statement `vault sync` runs on every other machine.

- **Detection** reads the working tree's record first; when it exists, that record is applied unchanged and the file is not rewritten (the vault wins over a fresh detection — no reopening, no churn). Otherwise it builds a fresh open record; if the graph already has the node (detected before records existed), the node's own fields — status above all — are kept, as the old `ON CREATE SET` kept them.
- **Resolution** updates the record (from the file, or from the graph for a pre-record conflict) and applies it. A projection conflict (`_source: 'projection'`) has no record — it is derived and rebuilt — so its status stays a graph-only write, as before, and the "no such conflict" error is unchanged.

`test_resolve_conflict_sets_status_and_provenance` looked at `session.runs[0]`; the resolution now reads the record first, so it finds the status write by its Cypher instead.

**Files:**
- Modify: `artmind/conflicts.py`
- Test: `test/test_conflict_resolution.py`
- Test: `test/test_conflicts.py`

- [ ] **Step 1: Write the failing tests**

**Replace in** `test/test_conflict_resolution.py` (lines 30-35):

```python


def test_resolve_conflict_sets_status_and_provenance(monkeypatch):
    session = FakeSession()
    monkeypatch.setattr(c, "neo4j_session", lambda: session)

```

**with:**

```python


def test_resolve_conflict_sets_status_and_provenance(monkeypatch):
    """A conflict with no record (a projection conflict: derived, rebuilt with
    the projection) keeps its graph-only status write."""
    session = FakeSession()
    monkeypatch.setattr(c, "neo4j_session", lambda: session)

```

**Replace in** `test/test_conflict_resolution.py` (lines 37-43):

```python

    assert out["id"] == "abc123"
    assert out["status"] == "resolved"
    cypher, kwargs = session.runs[0]
    assert "co.status = $status" in cypher
    assert "co.resolved_at" in cypher
    assert kwargs["status"] == "resolved"
```

**with:**

```python

    assert out["id"] == "abc123"
    assert out["status"] == "resolved"
    cypher, kwargs = next((cy, kw) for cy, kw in session.runs if "co.status = $status" in cy)
    assert "co.status = $status" in cypher
    assert "co.resolved_at" in cypher
    assert kwargs["status"] == "resolved"
```

**Replace in** `test/test_conflict_resolution.py` (lines 77-79):

```python
    assert result.exit_code != 0
    assert "abc123" in result.output
    assert "No Conflict node" in result.output
```

**with:**

```python
    assert result.exit_code != 0
    assert "abc123" in result.output
    assert "No Conflict node" in result.output


# ── an adjudicator conflict's resolution travels as its record (spec §14 A6) ──


import json

import pytest

from artmind import conflict_records, curation_records


@pytest.fixture()
def curation_dir(tmp_path, monkeypatch):
    import paths

    target = tmp_path / "curation"
    monkeypatch.setattr(paths, "CURATION_DIR", target)
    return target


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def single(self):
        return self._rows[0] if self._rows else None

    def data(self):
        return self._rows


class RecordingSession(FakeSession):
    """Answers `record_from_graph`'s read with `graph_node` (None: no node)."""

    def __init__(self, graph_node=None):
        super().__init__()
        self._graph_node = graph_node

    def run(self, cypher, **kwargs):
        self.runs.append((cypher, kwargs))
        if "properties(co) AS co" in cypher:
            return _Rows([self._graph_node] if self._graph_node else [])
        return _Rows([])


def _stored(curation_dir, **overrides):
    record = {
        "id": "abc123", "verdict": "conflicting_claims", "aspect": "limit", "claim_a": "A", "claim_b": "B",
        "severity": "high", "entity_class": "POLICY",
        "entities": [{"side": "a", "id": "ea", "key": "a|POLICY|x", "name": "A", "domain": "x"},
                     {"side": "b", "id": "eb", "key": "b|POLICY|y", "name": "B", "domain": "y"}],
        "domains": ["x", "y"], "evidence": [], "status": "open", "resolution_reason": None,
        "resolved_at": None, "detected_at": "t0", "detected_by_model": "m", "source": "adjudicator",
    }
    record.update(overrides)
    curation_records.write_record("conflicts", record)
    return record


def _applied(session):
    return [kw for cy, kw in session.runs if cy.strip().startswith("MERGE (co:Conflict {id: $id})")]


def test_resolving_a_recorded_conflict_updates_its_file_and_applies_it(monkeypatch, curation_dir):
    _stored(curation_dir)
    session = RecordingSession()
    monkeypatch.setattr(c, "neo4j_session", lambda: session)

    out = c.resolve_conflict("abc123", "dismissed", reason="not a real clash")

    record = json.loads((curation_dir / "conflicts" / "abc123.json").read_text())
    assert (record["status"], record["resolution_reason"]) == ("dismissed", "not a real clash")
    assert record["resolved_at"]
    applied = _applied(session)
    assert len(applied) == 1
    assert (applied[0]["status"], applied[0]["resolution_reason"]) == ("dismissed", "not a real clash")
    assert applied[0]["fingerprint"] == curation_records.fingerprint(
        (curation_dir / "conflicts" / "abc123.json").read_bytes()
    )
    assert not [cy for cy, _ in session.runs if "properties(co) AS co" in cy], "the file is read, not the graph"
    assert out == {"id": "abc123", "status": "dismissed", "reason": "not a real clash"}


def test_resolving_a_recorded_conflict_this_graph_has_not_seen_yet_still_applies(monkeypatch, curation_dir):
    """The record arrived with a pull and `vault sync` has not run: the file
    is the truth, so resolving writes it and MERGEs the node."""
    _stored(curation_dir)
    session = RecordingSession(graph_node=None)
    monkeypatch.setattr(c, "neo4j_session", lambda: session)

    c.resolve_conflict("abc123", "resolved")

    assert _applied(session)[0]["status"] == "resolved"


def test_resolving_a_conflict_detected_before_records_writes_its_first_record(monkeypatch, curation_dir):
    session = RecordingSession(graph_node={
        "co": {"id": "abc123", "aspect": "limit", "claim_a": "A", "claim_b": "B", "severity": "high",
               "status": "open", "detected_at": "t0", "detected_by_model": "m", "domains": ["x", "y"],
               "_source": "adjudicator"},
        "entities": [{"id": "ea", "key": "a|POLICY|x", "name": "A", "domain": "x"},
                     {"id": "eb", "key": "b|POLICY|y", "name": "B", "domain": "y"}],
        "evidence": [],
    })
    monkeypatch.setattr(c, "neo4j_session", lambda: session)

    c.resolve_conflict("abc123", "resolved", reason="ok")

    record = json.loads((curation_dir / "conflicts" / "abc123.json").read_text())
    assert record["status"] == "resolved"
    assert [e["id"] for e in record["entities"]] == ["ea", "eb"]
    assert _applied(session)[0]["idA"] == "ea"
```

**Replace in** `test/test_conflicts.py` (lines 145-147):

```python
    cid = c.materialize(FakeSession(), pair, verdict, [], [], "m")
    assert cid is None
    assert calls["supersede"] == 1
```

**with:**

```python
    cid = c.materialize(FakeSession(), pair, verdict, [], [], "m")
    assert cid is None
    assert calls["supersede"] == 1



# ── materialize writes the conflict's record and applies it (spec §14 A6) ────


import json as _json

import pytest as _pytest

from artmind import curation_records as _curation_records


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def single(self):
        return self._rows[0] if self._rows else None

    def data(self):
        return self._rows


class _MaterializeSession:
    def __init__(self, graph_node=None):
        self.calls = []
        self._graph_node = graph_node

    def run(self, cypher, **params):
        self.calls.append((cypher, params))
        if "properties(co) AS co" in cypher:
            return _Rows([self._graph_node] if self._graph_node else [])
        return _Rows([])

    def applied(self):
        return [p for c, p in self.calls if c.strip().startswith("MERGE (co:Conflict {id: $id})")]


_PAIR = {
    "id_a": "ea", "name_a": "Fee reversal", "domain_a": "banking.ops", "key_a": "fee reversal|POLICY|banking.ops",
    "id_b": "eb", "name_b": "Fee reversals", "domain_b": "banking.risk", "key_b": "fee reversals|POLICY|banking.risk",
    "entity_class": "POLICY",
}
_VERDICT = {"verdict": "conflicting_claims", "aspect": "approval limit", "claim_a": "CEO", "claim_b": "Manager", "severity": "high"}


@_pytest.fixture()
def curation_dir(tmp_path, monkeypatch):
    import paths

    target = tmp_path / "curation"
    monkeypatch.setattr(paths, "CURATION_DIR", target)
    return target


def test_materialize_writes_the_conflicts_record_and_applies_that_record(curation_dir):
    session = _MaterializeSession()

    cid = conflicts_mod.materialize(session, _PAIR, _VERDICT, [{"id": "d1_001", "doc_id": "d1"}], [], "m")

    assert cid == conflict_id("ea", "eb", "approval limit")
    path = curation_dir / "conflicts" / f"{cid}.json"
    record = _json.loads(path.read_text())
    assert (record["status"], record["claim_a"], record["claim_b"]) == ("open", "CEO", "Manager")
    applied = session.applied()
    assert len(applied) == 1
    assert (applied[0]["idA"], applied[0]["idB"], applied[0]["status"]) == ("ea", "eb", "open")
    assert applied[0]["fingerprint"] == _curation_records.fingerprint(path.read_bytes())


def test_re_detecting_a_recorded_conflict_never_reopens_or_rewrites_it(curation_dir):
    session = _MaterializeSession()
    cid = conflicts_mod.materialize(session, _PAIR, _VERDICT, [], [], "m")
    path = curation_dir / "conflicts" / f"{cid}.json"
    record = _json.loads(path.read_text())
    record.update(status="resolved", resolution_reason="settled", resolved_at="t1")
    _curation_records.write_record("conflicts", record)
    before = path.read_bytes()

    again = _MaterializeSession()
    conflicts_mod.materialize(again, _PAIR, dict(_VERDICT, claim_a="a different wording"), [], [], "other-model")

    assert path.read_bytes() == before
    assert again.applied()[0]["status"] == "resolved"
    assert again.applied()[0]["claim_a"] == "CEO"
    assert not [c for c, _ in again.calls if "properties(co) AS co" in c], "the file wins; the graph is not read"


def test_re_detecting_a_conflict_that_predates_records_keeps_the_nodes_status(curation_dir):
    session = _MaterializeSession(graph_node={
        "co": {"id": "x", "aspect": "approval limit", "claim_a": "old A", "claim_b": "old B", "severity": "medium",
               "status": "dismissed", "resolution_reason": "noise", "resolved_at": "t1",
               "detected_at": "t0", "detected_by_model": "old-model", "_source": "adjudicator"},
        "entities": [], "evidence": [],
    })

    cid = conflicts_mod.materialize(session, _PAIR, _VERDICT, [], [], "m")

    record = _json.loads((curation_dir / "conflicts" / f"{cid}.json").read_text())
    assert (record["status"], record["claim_a"], record["detected_at"]) == ("dismissed", "old A", "t0")
    assert [e["id"] for e in record["entities"]] == ["ea", "eb"], "the entities come from this detection"
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --group dev pytest test/test_conflicts.py test/test_conflict_resolution.py -q`
Expected: `6 failed, 15 passed` — the three resolution tests with `ValueError: No Conflict node with id 'abc123'` (no record is read yet), the three detection tests with `FileNotFoundError` on the record file (nothing writes it yet). `test_resolve_conflict_sets_status_and_provenance` already passes with its new lookup.

- [ ] **Step 3: Implement**

**Replace in** `artmind/conflicts.py` (lines 271-309):

```python
        return None
    if verdict["verdict"] != "conflicting_claims":
        return None
    cid = conflict_id(pair["id_a"], pair["id_b"], verdict["aspect"] or pair["entity_class"])
    domains = sorted({pair["domain_a"], pair["domain_b"]})
    session.run(
        """
        MERGE (co:Conflict {id:$id})
        ON CREATE SET co.aspect=$aspect, co.claim_a=$claim_a, co.claim_b=$claim_b,
                      co.severity=$severity, co.status='open', co.domains=$domains,
                      co.detected_at=$now, co.detected_by_model=$model,
                      co._source='adjudicator'
        WITH co
        MATCH (a:Entity {_id:$idA}), (b:Entity {_id:$idB})
        MERGE (co)-[:CONFLICT_OF]->(a)
        MERGE (co)-[:CONFLICT_OF]->(b)
        MERGE (a)-[ra:CONFLICTS_WITH]->(b) SET ra.conflict_id=$id, ra.aspect=$aspect
        MERGE (b)-[rb:CONFLICTS_WITH]->(a) SET rb.conflict_id=$id, rb.aspect=$aspect
        """,
        id=cid, aspect=verdict["aspect"], claim_a=verdict["claim_a"], claim_b=verdict["claim_b"],
        severity=verdict["severity"], domains=domains,
        now=datetime.now(timezone.utc).isoformat(), model=model,
        idA=pair["id_a"], idB=pair["id_b"],
    )
    for side, chunks in (("a", evidence_a), ("b", evidence_b)):
        for c in chunks:
            session.run(
                """
                MATCH (co:Conflict {id:$id}), (c:DocChunk {id:$cid})
                MERGE (co)-[e:EVIDENCE {side:$side}]->(c)
                """,
                id=cid, cid=c["id"], side=side,
            )
    return cid


def detect_conflicts(
    domains: list[str],
    name_filter: str | None = None,
```

**with:**

```python
        return None
    if verdict["verdict"] != "conflicting_claims":
        return None
    from artmind import conflict_records, curation_records

    cid = conflict_id(pair["id_a"], pair["id_b"], verdict["aspect"] or pair["entity_class"])
    # The conflict travels as a vault file (spec 2026-09-26 §14 A6), and the
    # vault's record wins over a fresh detection: re-detecting a conflict
    # another machine already recorded -- and perhaps resolved -- must neither
    # reopen it nor rewrite its file.
    record = curation_records.read_record(conflict_records.NAME, cid)
    if record is None:
        record = conflict_records.record_from_detection(
            cid, pair, verdict, evidence_a, evidence_b, model, datetime.now(timezone.utc).isoformat(),
        )
        existing = conflict_records.record_from_graph(session, cid)
        if existing is not None:
            # Detected before records existed: the node's own fields survive,
            # exactly as the old `ON CREATE SET` kept them.
            for name in _KEPT_ON_REDETECTION:
                record[name] = existing[name]
    fingerprint = curation_records.write_record(conflict_records.NAME, record)
    conflict_records.apply(session, record, fingerprint)
    return cid


#: A re-detected conflict whose `:Conflict` node predates its record keeps
#: these from the node -- above all its status: a re-detection never reopens.
_KEPT_ON_REDETECTION = (
    "aspect", "claim_a", "claim_b", "severity", "status", "resolution_reason",
    "resolved_at", "detected_at", "detected_by_model",
)


def detect_conflicts(
    domains: list[str],
    name_filter: str | None = None,
```

**Replace in** `artmind/conflicts.py` (lines 404-419):

```python
    re-detection pass cannot adjudicate — closing it is a human judgment, so it
    is an explicit command and never a side effect.

    Raises ValueError when the id matches no Conflict node. That includes the
    orphaned-edge case: a CONFLICTS_WITH edge whose Conflict node was deleted
    still surfaces in list_conflicts (reported as 'open' via coalesce) but has
    nowhere to record a status.
    """
    if status not in RESOLUTION_STATUSES:
        raise ValueError(
            f"status must be one of {', '.join(RESOLUTION_STATUSES)}; got {status!r}"
        )
    with neo4j_session() as session:
        rec = session.run(
            """
            MATCH (co:Conflict {id: $id})
```

**with:**

```python
    re-detection pass cannot adjudicate — closing it is a human judgment, so it
    is an explicit command and never a side effect.

    An adjudicator conflict's resolution travels (spec 2026-09-26 §14 A6):
    its record file under `.artmind/data/curation/conflicts/` is updated --
    or, for a conflict detected before records existed, written for the
    first time from the graph -- and the graph is written from that record,
    the same path `vault sync` applies on every other machine. A projection
    conflict (`_source: 'projection'`) has no record: it is derived from
    observations and rebuilt with the projection, so its status stays a
    graph-only write, as before.

    Raises ValueError when the id matches neither a record nor a Conflict
    node. That includes the orphaned-edge case: a CONFLICTS_WITH edge whose
    Conflict node was deleted still surfaces in list_conflicts (reported as
    'open' via coalesce) but has nowhere to record a status.
    """
    if status not in RESOLUTION_STATUSES:
        raise ValueError(
            f"status must be one of {', '.join(RESOLUTION_STATUSES)}; got {status!r}"
        )
    from artmind import conflict_records, curation_records

    now = datetime.now(timezone.utc).isoformat()
    with neo4j_session() as session:
        record = curation_records.read_record(conflict_records.NAME, conflict_id)
        if record is None:
            record = conflict_records.record_from_graph(session, conflict_id)
        if record is not None:
            record.update(status=status, resolution_reason=reason, resolved_at=now)
            fingerprint = curation_records.write_record(conflict_records.NAME, record)
            conflict_records.apply(session, record, fingerprint)
            logger.info("conflict {} → {} ({}), recorded in the vault", conflict_id, status, reason or "no reason given")
            return {"id": conflict_id, "status": status, "reason": reason}
        rec = session.run(
            """
            MATCH (co:Conflict {id: $id})
```

**Replace in** `artmind/conflicts.py` (lines 424-430):

```python
            """,
            id=conflict_id,
            status=status,
            now=datetime.now(timezone.utc).isoformat(),
            reason=reason,
        ).single()
    if not rec:
```

**with:**

```python
            """,
            id=conflict_id,
            status=status,
            now=now,
            reason=reason,
        ).single()
    if not rec:
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --group dev pytest test/test_conflicts.py test/test_conflict_resolution.py -q`
Expected: `21 passed`. Then the full suite — all pass.

- [ ] **Step 5: Commit**

```bash
git add artmind/conflicts.py test/test_conflict_resolution.py test/test_conflicts.py
git commit -m "feat(conflicts): detection and resolution write the conflict's vault record and apply it"
```

---

### Task 5: track C — apply added/changed curation records, remove deleted ones (spec §7, §14 A6)

`classify_diff` gains `plan.curation`: `(kind, record_id, "apply" | "remove")` for every record file of a registered kind added, changed or deleted in the range (`--domain` reads the record — at HEAD for an apply, at the base for a removal — and keeps it when any of its domains is in scope). Then, like Plan B's document skip, `drop_unchanged_curation` removes every record whose committed fingerprint the graph already carries (one fingerprint read per kind), so a shared graph skips what the writing machine applied.

`sync()` applies each record from its **committed bytes at HEAD** (and removes each deleted one using its bytes at the base, the last version that existed), one write transaction per record: `"pre"` kinds before the union rebuild (their keys join it), `"post"` kinds after it. A record whose id disagrees with its file name fails the sync, bookmarks untouched. Dry run and real run report the same `curation` counts, plus `curation_unchanged`.

**Files:**
- Modify: `artmind/vault_sync.py`
- Test: `test/test_vault_sync.py`

- [ ] **Step 1: Write the failing tests**

**Replace in** `test/test_vault_sync.py` (lines 3190-3192):

```python
    assert read_state(VaultLayout(repo)) == {"last_synced_commit": base}
    real = vs.sync(repo)
    assert (real["same_as_groups"], real["same_as_keys"]) == (dry["same_as_groups"], dry["same_as_keys"])
```

**with:**

```python
    assert read_state(VaultLayout(repo)) == {"last_synced_commit": base}
    real = vs.sync(repo)
    assert (real["same_as_groups"], real["same_as_keys"]) == (dry["same_as_groups"], dry["same_as_keys"])


# ── track C: curation records (spec 2026-09-26 §7, §14 A6) ──────────────────


class _Rows:
    def __init__(self, rows=()):
        self._rows = list(rows)

    def single(self):
        return self._rows[0] if self._rows else None

    def data(self):
        return list(self._rows)


class _CurationGraph:
    """The graph side of curation records: answers the fingerprint read from
    `fingerprints`, records every statement, and runs write transactions
    against itself. `events` is shared with other fakes to check ordering."""

    def __init__(self, events):
        self.calls = []
        self.fingerprints: dict[str, str | None] = {}
        self.events = events

    def run(self, cypher, **params):
        self.calls.append((cypher, params))
        if "record_fingerprint AS fingerprint" in cypher:
            return _Rows({"id": i, "fingerprint": self.fingerprints[i]} for i in params["ids"] if i in self.fingerprints)
        if cypher.strip().startswith("MERGE (co:Conflict {id: $id})"):
            self.events.append(("apply", params["id"]))
        if "DETACH DELETE co" in cypher:
            self.events.append(("remove", params["id"]))
        return _Rows()

    def execute_write(self, fn, *args, **kwargs):
        return fn(self, *args, **kwargs)

    def applied(self):
        return [p for c, p in self.calls if c.strip().startswith("MERGE (co:Conflict {id: $id})")]


@pytest.fixture()
def curation_graph(monkeypatch):
    from contextlib import contextmanager

    import artmind.graph_query as graph_query

    fake = _CurationGraph(events=[])

    @contextmanager
    def _session(access_mode=None, *, timeout=None):
        yield fake

    monkeypatch.setattr(graph_query, "neo4j_session", _session)
    return fake


def _patch_curation_dir(monkeypatch, vault_dir):
    import paths

    target = vault_dir / ".artmind" / "data" / "curation"
    monkeypatch.setattr(paths, "CURATION_DIR", target)
    return target


def _conflict(record_id, *, status="open", domains=("banking.ops", "banking.risk")):
    return {
        "id": record_id, "verdict": "conflicting_claims", "aspect": "limit", "claim_a": "A", "claim_b": "B",
        "severity": "high", "entity_class": "POLICY",
        "entities": [{"side": "a", "id": "ea", "key": "a|POLICY|x", "name": "A", "domain": domains[0]},
                     {"side": "b", "id": "eb", "key": "b|POLICY|y", "name": "B", "domain": domains[-1]}],
        "domains": list(domains), "evidence": [], "status": status, "resolution_reason": None,
        "resolved_at": None, "detected_at": "t0", "detected_by_model": "m", "source": "adjudicator",
    }


def _curation_base(repo, monkeypatch, *records):
    from artmind import curation_records

    _patch_kg_dir(monkeypatch, repo)
    _patch_structured_text_dir(monkeypatch, repo)
    _patch_curation_dir(monkeypatch, repo)
    for record in records:
        curation_records.write_record("conflicts", record)
    (repo / "a.txt").write_text("x")
    _commit_all(repo, "base")
    return vs.head_sha(repo)


def test_classify_diff_applies_added_and_changed_records_and_removes_deleted_ones(repo, monkeypatch):
    from artmind import curation_records

    base = _curation_base(repo, monkeypatch, _conflict("c1"), _conflict("c2"))
    curation_records.write_record("conflicts", _conflict("c1", status="resolved"))
    curation_records.delete_record("conflicts", "c2")
    curation_records.write_record("conflicts", _conflict("c3"))
    curation = repo / ".artmind" / "data" / "curation"
    (curation / "conflicts" / "notes.txt").write_text("ignored")
    (curation / "unknown_kind").mkdir()
    (curation / "unknown_kind" / "x.json").write_text("{}")
    _commit_all(repo, "resolve c1, drop c2, add c3")

    plan = vs.classify_diff(repo, base, vs.head_sha(repo))

    assert sorted(plan.curation) == [
        ("conflicts", "c1", "apply"), ("conflicts", "c2", "remove"), ("conflicts", "c3", "apply"),
    ]


def test_classify_diff_scopes_curation_by_the_records_domains(repo, monkeypatch):
    from artmind import curation_records

    base = _curation_base(repo, monkeypatch, _conflict("gone", domains=("legal",)))
    curation_records.write_record("conflicts", _conflict("bank", domains=("banking.ops", "legal")))
    curation_records.write_record("conflicts", _conflict("law", domains=("legal",)))
    curation_records.delete_record("conflicts", "gone")
    _commit_all(repo, "two added, one removed")

    assert sorted(vs.classify_diff(repo, base, vs.head_sha(repo), domains=["banking"]).curation) == [
        ("conflicts", "bank", "apply"),
    ]
    assert sorted(vs.classify_diff(repo, base, vs.head_sha(repo), domains=["legal"]).curation) == [
        ("conflicts", "bank", "apply"), ("conflicts", "gone", "remove"), ("conflicts", "law", "apply"),
    ]


def test_sync_applies_a_record_as_committed_not_as_in_the_working_tree(repo, monkeypatch, curation_graph):
    from artmind import curation_records

    base = _curation_base(repo, monkeypatch)
    curation_records.write_record("conflicts", _conflict("c1", status="resolved"))
    _commit_all(repo, "c1 resolved")
    committed = (repo / ".artmind" / "data" / "curation" / "conflicts" / "c1.json").read_bytes()
    curation_records.write_record("conflicts", _conflict("c1", status="dismissed"))   # uncommitted
    from artmind.vault import VaultLayout, write_state
    write_state(VaultLayout(repo), {"last_synced_commit": base})
    _patch_ingest_and_projection(monkeypatch)

    result = vs.sync(repo)

    applied = curation_graph.applied()
    assert [(p["id"], p["status"]) for p in applied] == [("c1", "resolved")]
    assert applied[0]["fingerprint"] == curation_records.fingerprint(committed)
    assert result["curation"] == {"conflicts": {"apply": 1, "remove": 0}}
    assert _bookmarks(repo) == (vs.head_sha(repo), vs.head_sha(repo))


def test_sync_removes_a_deleted_record(repo, monkeypatch, curation_graph):
    from artmind import curation_records

    base = _curation_base(repo, monkeypatch, _conflict("c1"))
    curation_records.delete_record("conflicts", "c1")
    _commit_all(repo, "drop c1")
    from artmind.vault import VaultLayout, write_state
    write_state(VaultLayout(repo), {"last_synced_commit": base})
    _patch_ingest_and_projection(monkeypatch)

    vs.sync(repo)

    assert curation_graph.events == [("remove", "c1")]
    assert ("MATCH (:Entity)-[r:CONFLICTS_WITH {conflict_id: $id}]->(:Entity) DELETE r", {"id": "c1"}) in curation_graph.calls


def test_sync_skips_a_record_the_shared_graph_already_carries(repo, monkeypatch, curation_graph):
    from artmind import curation_records

    base = _curation_base(repo, monkeypatch)
    fp = curation_records.write_record("conflicts", _conflict("c1"))
    curation_records.write_record("conflicts", _conflict("c2"))
    _commit_all(repo, "two conflicts detected on the other machine")
    curation_graph.fingerprints = {"c1": fp, "c2": "an older version"}
    from artmind.vault import VaultLayout, write_state
    write_state(VaultLayout(repo), {"last_synced_commit": base})
    _patch_ingest_and_projection(monkeypatch)

    result = vs.sync(repo)

    assert [p["id"] for p in curation_graph.applied()] == ["c2"]
    assert result["curation_unchanged"] == 1
    reads = [p for c, p in curation_graph.calls if "record_fingerprint AS fingerprint" in c]
    assert reads == [{"ids": ["c1", "c2"]}], "one fingerprint read for the kind"


def test_conflicts_are_applied_after_the_projection_rebuild(repo, monkeypatch, curation_graph):
    """CONFLICTS_WITH joins two :Entity nodes the rebuild creates on a local
    graph; applied first, it would MATCH nothing."""
    from artmind import curation_records
    import artmind.table2graph as t2g

    kg_dir = _patch_kg_dir(monkeypatch, repo)
    _patch_structured_text_dir(monkeypatch, repo)
    _patch_curation_dir(monkeypatch, repo)
    _write_doc_folder(kg_dir, "banking", "doc1", "docid-1")
    curation_records.write_record("conflicts", _conflict("c1"))
    _commit_all(repo, "a doc and a conflict")
    _patch_ingest_and_projection(monkeypatch, deferred_keys_by_doc={"doc1": [["a", "POLICY", "x"]]})
    monkeypatch.setattr(
        t2g, "_rebuild_in_batches",
        lambda keys, groups=None: curation_graph.events.append(("rebuild", len(keys))) or {"keys": len(keys)},
    )

    vs.sync(repo, bootstrap_empty=True)

    assert curation_graph.events == [("rebuild", 1), ("apply", "c1")]


def test_a_curation_dry_run_reports_what_the_real_run_applies_and_writes_nothing(repo, monkeypatch, curation_graph):
    from artmind import curation_records

    base = _curation_base(repo, monkeypatch, _conflict("c1"))
    curation_records.write_record("conflicts", _conflict("c2"))
    curation_records.delete_record("conflicts", "c1")
    _commit_all(repo, "c2 in, c1 out")
    from artmind.vault import VaultLayout, write_state
    write_state(VaultLayout(repo), {"last_synced_commit": base})
    _patch_ingest_and_projection(monkeypatch)

    dry = vs.sync(repo, dry_run=True)

    assert dry["curation"] == {"conflicts": {"apply": 1, "remove": 1}}
    assert curation_graph.events == []
    real = vs.sync(repo)
    assert real["curation"] == dry["curation"]
    assert sorted(curation_graph.events) == [("apply", "c2"), ("remove", "c1")]


def test_a_record_whose_id_disagrees_with_its_file_name_fails_the_sync(repo, monkeypatch, curation_graph):
    base = _curation_base(repo, monkeypatch)
    folder = repo / ".artmind" / "data" / "curation" / "conflicts"
    folder.mkdir(parents=True, exist_ok=True)
    import json
    (folder / "c1.json").write_text(json.dumps(_conflict("someone-else")))
    _commit_all(repo, "a hand-edited record")
    from artmind.vault import VaultLayout, write_state
    write_state(VaultLayout(repo), {"last_synced_commit": base})
    _patch_ingest_and_projection(monkeypatch)

    with pytest.raises(vs.VaultSyncError, match="not a conflicts record for id 'c1'"):
        vs.sync(repo)
    assert _bookmarks(repo) == (None, None)
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --group dev pytest test/test_vault_sync.py -q`
Expected: `8 failed, 148 passed` — the two classifier tests with `AttributeError: 'SyncPlan' object has no attribute 'curation'`; the `sync()` tests with `AssertionError` (no record applied or removed), `KeyError: 'curation'` (dry run) and `Failed: DID NOT RAISE VaultSyncError` (the mismatched id).

- [ ] **Step 3: Implement**

**Replace in** `artmind/vault_sync.py` (lines 257-262):

```python
    regenerate_tables: list[tuple[str, str]] = field(default_factory=list)  # (domain, table_name)
    same_as_keys: list[tuple[str, str, str]] = field(default_factory=list)  # track D: keys to rebuild
    same_as_groups: int = 0                                                 # track D: groups added/removed/changed


def _in_scope(domain: str, domains: list[str] | None) -> bool:
```

**with:**

```python
    regenerate_tables: list[tuple[str, str]] = field(default_factory=list)  # (domain, table_name)
    same_as_keys: list[tuple[str, str, str]] = field(default_factory=list)  # track D: keys to rebuild
    same_as_groups: int = 0                                                 # track D: groups added/removed/changed
    curation: list[tuple[str, str, str]] = field(default_factory=list)      # track C: (kind, record_id, "apply"|"remove")


def _in_scope(domain: str, domains: list[str] | None) -> bool:
```

**Replace in** `artmind/vault_sync.py` (lines 795-800):

```python
    return keys, len(changed)


def classify_diff(
    vault_dir: Path, base: str, head: str, domains: list[str] | None = None
) -> SyncPlan:
```

**with:**

```python
    return keys, len(changed)


# ── track C: curation records (spec 2026-09-26 §7, §14 A6) ───────────────────


def _curation_rel(vault_dir: Path) -> Path | None:
    """`paths.CURATION_DIR` relative to the vault, or None outside it."""
    import paths

    try:
        return Path(paths.CURATION_DIR).relative_to(vault_dir)
    except ValueError:
        return None


def _curation_relpath(vault_dir: Path, kind: str, record_id: str) -> str:
    return str(_curation_rel(vault_dir) / kind / f"{record_id}.json")


def _blobs_at(vault_dir: Path, rev: str, relpaths: list[str]) -> dict[str, bytes]:
    """`{relpath: committed bytes}` for the files among `relpaths` that exist
    at `rev`: one `git ls-tree` and one `git cat-file --batch`, raw blobs
    (the bytes `curation_records.fingerprint` hashes)."""
    if not relpaths:
        return {}
    listing = _git(vault_dir, ["ls-tree", "-r", "-z", rev, "--", *relpaths])
    oid_of: dict[str, str] = {}
    for entry in listing.split("\0"):
        if not entry:
            continue
        meta, _, path = entry.partition("\t")
        fields = meta.split(" ")
        if len(fields) == 3 and fields[1] == "blob":
            oid_of[path] = fields[2]
    blobs = _cat_blobs(vault_dir, list(oid_of.values()))
    return {path: blobs[oid] for path, oid in oid_of.items() if oid in blobs}


def _classify_curation(
    vault_dir: Path, base: str, head: str, domains: list[str] | None
) -> list[tuple[str, str, str]]:
    """Track C: `(kind, record_id, action)` for every curation record file
    (`curation/<kind>/<id>.json`, a registered kind) added or changed
    (`"apply"`) or deleted (`"remove"`) between `base` and `head`.

    `--domain` scoping reads the record -- as committed at `head` for an
    apply, at `base` for a removal -- and keeps it when any of its domains is
    in scope."""
    from artmind import curation_records

    rel = _curation_rel(vault_dir)
    if rel is None:
        return []
    known = curation_records.kinds()
    changes: list[tuple[str, str, str]] = []
    for status, path in _diff_name_status(vault_dir, base, head, vault_dir / rel):
        parts = Path(path).relative_to(rel).parts
        if len(parts) != 2 or not parts[1].endswith(".json") or parts[0] not in known:
            continue
        changes.append((parts[0], parts[1][: -len(".json")], "remove" if status == "D" else "apply"))
    if not domains or not changes:
        return changes
    at_head = _blobs_at(vault_dir, head, [_curation_relpath(vault_dir, k, i) for k, i, a in changes if a == "apply"])
    at_base = _blobs_at(vault_dir, base, [_curation_relpath(vault_dir, k, i) for k, i, a in changes if a == "remove"])
    scoped = []
    for kind, record_id, action in changes:
        blobs = at_head if action == "apply" else at_base
        record = curation_records.parse(blobs.get(_curation_relpath(vault_dir, kind, record_id)))
        if record is not None and any(_in_scope(d, domains) for d in known[kind].domains(record)):
            scoped.append((kind, record_id, action))
    return scoped


def committed_curation_fingerprints(vault_dir: Path, head: str, plan: "SyncPlan") -> dict[tuple[str, str], str]:
    """`{(kind, record_id): fingerprint}` of every record `plan` applies, as
    committed at `head`."""
    from artmind import curation_records

    wanted = {(k, i): _curation_relpath(vault_dir, k, i) for k, i, a in plan.curation if a == "apply"}
    blobs = _blobs_at(vault_dir, head, list(wanted.values()))
    return {key: curation_records.fingerprint(blobs[path]) for key, path in wanted.items() if path in blobs}


def _graph_curation_fingerprints(
    items: list[tuple[str, str, str]], *, timeout: float | None
) -> dict[tuple[str, str], str | None]:
    """`{(kind, record_id): fingerprint}` the graph holds, one query per kind
    that has items. A record the graph does not hold is absent."""
    from artmind import curation_records

    by_kind: dict[str, list[str]] = {}
    for kind, record_id, _ in items:
        by_kind.setdefault(kind, []).append(record_id)
    known = curation_records.kinds()
    found: dict[tuple[str, str], str | None] = {}
    for kind, ids in by_kind.items():
        for record_id, fp in known[kind].read_fingerprints(ids, timeout=timeout).items():
            found[(kind, record_id)] = fp
    return found


def drop_unchanged_curation(vault_dir: Path, plan: "SyncPlan", *, timeout: float | None = None) -> int:
    """Spec §6 A3 for curation: remove from `plan.curation` every record
    whose committed fingerprint the graph already carries (the machine that
    wrote it, sharing this graph, already applied it) and return how many.
    Removals are always kept (removing an absent record is a no-op)."""
    committed = committed_curation_fingerprints(vault_dir, plan.head, plan)
    applies = [item for item in plan.curation if item[2] == "apply"]
    if not applies:
        return 0
    in_graph = _graph_curation_fingerprints(applies, timeout=timeout)
    unchanged = [
        (k, i, a) for k, i, a in applies
        if committed.get((k, i)) is not None and in_graph.get((k, i)) == committed[(k, i)]
    ]
    plan.curation = [item for item in plan.curation if item not in unchanged]
    return len(unchanged)


def _curation_counts(items: list[tuple[str, str, str]]) -> dict[str, dict[str, int]]:
    counts: dict[str, dict[str, int]] = {}
    for kind, _, action in items:
        counts.setdefault(kind, {"apply": 0, "remove": 0})[action] += 1
    return counts


def _apply_curation(
    vault_dir: Path, base: str, head: str, items: list[tuple[str, str, str]], phase: str
) -> set:
    """Apply (or remove) every item of `phase` -- an applied record from its
    committed bytes at `head`, a removed one from its bytes at `base` (the
    last version that existed), one write transaction per record -- and
    return the aggregate keys the kinds report changed."""
    from artmind import curation_records, graph_query

    known = curation_records.kinds()
    items = [item for item in items if known[item[0]].phase == phase]
    if not items:
        return set()
    blobs = _blobs_at(vault_dir, head, [_curation_relpath(vault_dir, k, i) for k, i, a in items if a == "apply"])
    removed = _blobs_at(vault_dir, base, [_curation_relpath(vault_dir, k, i) for k, i, a in items if a == "remove"])
    keys: set = set()
    with graph_query.neo4j_session() as session:
        for kind_name, record_id, action in items:
            kind = known[kind_name]
            if action == "remove":
                record = curation_records.parse(removed.get(_curation_relpath(vault_dir, kind_name, record_id)))
                if record is None or record.get("id") != record_id:
                    record = {"id": record_id}
                keys |= set(session.execute_write(kind.remove, record) or ())
                continue
            relpath = _curation_relpath(vault_dir, kind_name, record_id)
            data = blobs.get(relpath)
            record = curation_records.parse(data)
            if record is None or record.get("id") != record_id:
                raise VaultSyncError(f"{relpath} at {head[:12]} is not a {kind_name} record for id {record_id!r}")
            keys |= set(session.execute_write(kind.apply, record, curation_records.fingerprint(data)) or ())
    return {tuple(k) for k in keys}


def classify_diff(
    vault_dir: Path, base: str, head: str, domains: list[str] | None = None
) -> SyncPlan:
```

**Replace in** `artmind/vault_sync.py` (lines 820-825):

```python
            seen.add((domain, doc_id))
            plan.retract.append((domain, doc_id))
    plan.same_as_keys, plan.same_as_groups = _classify_same_as(vault_dir, base, head, domains)
    return plan


```

**with:**

```python
            seen.add((domain, doc_id))
            plan.retract.append((domain, doc_id))
    plan.same_as_keys, plan.same_as_groups = _classify_same_as(vault_dir, base, head, domains)
    plan.curation = _classify_curation(vault_dir, base, head, domains)
    return plan


```

**Replace in** `artmind/vault_sync.py` (lines 1065-1073):

```python
    if "graph" in stores:
        plan = classify_diff(vault_dir, bases["graph"], head, domains)
        unchanged = drop_unchanged(vault_dir, plan)
    else:
        plan = SyncPlan(base=bases["structured"], head=head)
        unchanged = []
    project_tables = list(plan.regenerate_tables)
    if "structured" not in stores:
        structured_tables: list[tuple[str, str]] = []
```

**with:**

```python
    if "graph" in stores:
        plan = classify_diff(vault_dir, bases["graph"], head, domains)
        unchanged = drop_unchanged(vault_dir, plan)
        curation_unchanged = drop_unchanged_curation(vault_dir, plan)
    else:
        plan = SyncPlan(base=bases["structured"], head=head)
        unchanged = []
        curation_unchanged = 0
    project_tables = list(plan.regenerate_tables)
    if "structured" not in stores:
        structured_tables: list[tuple[str, str]] = []
```

**Replace in** `artmind/vault_sync.py` (lines 1118-1123):

```python
            "structured_only_tables": structured_only_dry,
            "same_as_groups": plan.same_as_groups,
            "same_as_keys": len(plan.same_as_keys),
            "cursor_would_advance": not domains,
        }

```

**with:**

```python
            "structured_only_tables": structured_only_dry,
            "same_as_groups": plan.same_as_groups,
            "same_as_keys": len(plan.same_as_keys),
            "curation": _curation_counts(plan.curation),
            "curation_unchanged": curation_unchanged,
            "cursor_would_advance": not domains,
        }

```

**Replace in** `artmind/vault_sync.py` (lines 1200-1205):

```python
    # ── track D: every key of a same-as group added/removed/changed ─────────
    all_keys.update(tuple(k) for k in plan.same_as_keys)

    # ── the union rebuild (§5 step 4), chunked exactly like table2graph's own ─
    #    With the same-as groups as committed at `head`, never the working
    #    tree's `same_as.yaml`; and every group touching a key is rebuilt
```

**with:**

```python
    # ── track D: every key of a same-as group added/removed/changed ─────────
    all_keys.update(tuple(k) for k in plan.same_as_keys)

    # ── track C, "pre" kinds: curation the rebuild must see ──────────────────
    all_keys.update(_apply_curation(vault_dir, plan.base, head, plan.curation, "pre"))

    # ── the union rebuild (§5 step 4), chunked exactly like table2graph's own ─
    #    With the same-as groups as committed at `head`, never the working
    #    tree's `same_as.yaml`; and every group touching a key is rebuilt
```

**Replace in** `artmind/vault_sync.py` (lines 1214-1219):

```python
    else:
        projection_summary = {"rebuilt": 0, "deleted": 0, "absent": 0, "keys": 0, "batches": 0}

    # ── domain-scoped embed sweeps (§5 step 5) ──────────────────────────────
    touched_domains = sorted({k[2] for k in all_keys})
    for d in touched_domains:
```

**with:**

```python
    else:
        projection_summary = {"rebuilt": 0, "deleted": 0, "absent": 0, "keys": 0, "batches": 0}

    # ── track C, "post" kinds: curation that joins rebuilt entities ─────────
    _apply_curation(vault_dir, plan.base, head, plan.curation, "post")

    # ── domain-scoped embed sweeps (§5 step 5) ──────────────────────────────
    touched_domains = sorted({k[2] for k in all_keys})
    for d in touched_domains:
```

**Replace in** `artmind/vault_sync.py` (lines 1248-1253):

```python
        "structured_only_tables": structured_only,
        "same_as_groups": plan.same_as_groups,
        "same_as_keys": len(plan.same_as_keys),
        "projection": projection_summary,
        "domains_swept": touched_domains,
    }
```

**with:**

```python
        "structured_only_tables": structured_only,
        "same_as_groups": plan.same_as_groups,
        "same_as_keys": len(plan.same_as_keys),
        "curation": _curation_counts(plan.curation),
        "curation_unchanged": curation_unchanged,
        "projection": projection_summary,
        "domains_swept": touched_domains,
    }
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --group dev pytest test/test_vault_sync.py -q`
Expected: `156 passed`. Then the full suite — all pass.

- [ ] **Step 5: Commit**

```bash
git add artmind/vault_sync.py test/test_vault_sync.py
git commit -m "feat(vault-sync): track C -- apply added/changed curation records and remove deleted ones, fingerprint-skipped"
```

---

### Task 6: an update staging folder commits as a `:UserChat` through `_commit_document_tx`

Decision 1's single code path, graph side. `_commit_document_tx` reads `document.json`'s `source_kind`; `"user_chat"` (`ingest.USER_CHAT_SOURCE_KIND`) commits the document as a `:UserChat` (revived from `:UserChatHistory` like a Document from `:DocumentHistory`), records the fingerprint there, and links every observation `EXTRACTED_FROM` it. Everything else — prior-version demotion, retractions, relationship observations, the same-as-aware affected-key rebuild — is the document path, unchanged. `sync_state` reads fingerprints from `:Document` and `:UserChat` in one `UNION ALL` query; `retract_document` also moves a `:UserChat` to `:UserChatHistory`; `setup` constrains `UserChatHistory.id`; graph snapshots carry the new label.

**Files:**
- Modify: `artmind/graph_snapshot.py`
- Modify: `artmind/ingest.py`
- Modify: `artmind/setup.py`
- Modify: `artmind/sync_state.py`
- Test: `test/test_graph_snapshot.py`
- Test: `test/test_user_chat_commit.py` (new)

- [ ] **Step 1: Write the failing tests**

**Replace in** `test/test_graph_snapshot.py` (lines 183-189):

```python
        assert set(params["base_labels"]) == {
            "Document", "DocumentHistory",
            "DocChunk", "DocChunkHistory",
            "UserChat",
            "Observation", "ObservationHistory",
            "Synthesis",
            "Entity", "Conflict", "ProjectionState",
```

**with:**

```python
        assert set(params["base_labels"]) == {
            "Document", "DocumentHistory",
            "DocChunk", "DocChunkHistory",
            "UserChat", "UserChatHistory",
            "Observation", "ObservationHistory",
            "Synthesis",
            "Entity", "Conflict", "ProjectionState",
```

**Create** `test/test_user_chat_commit.py`:

```python
"""An `artmind update` staging folder commits through the same transaction as
every document (`ingest._commit_document_tx`), as a `:UserChat` rather than a
`:Document` -- the one code path `update confirm` and `vault sync` share
(spec 2026-09-26 §7). A recording session stands in for Neo4j; the tests
assert on the Cypher and parameters sent (CLAUDE.md §3)."""
import json
from contextlib import contextmanager

import pytest

import artmind.ingest as ing
from artmind import sync_state


class _Result:
    def single(self):
        return None

    def data(self):
        return []

    def __iter__(self):
        return iter(())


class _Session:
    def __init__(self):
        self.calls = []

    def run(self, cypher, **params):
        self.calls.append((cypher, params))
        return _Result()

    def execute_write(self, fn, *args, **kwargs):
        return fn(self, *args, **kwargs)

    execute_read = execute_write

    def matching(self, text):
        return [(c, p) for c, p in self.calls if text in c]


@pytest.fixture()
def session(monkeypatch):
    import artmind.graph_query as graph_query

    fake = _Session()

    @contextmanager
    def _fake(*args, **kwargs):
        yield fake

    monkeypatch.setattr(graph_query, "neo4j_session", _fake)
    monkeypatch.setattr(ing, "_ensure_neo4j_schema", lambda *a, **k: None)
    return fake


_OBS = {"id": "o1", "key": "alice|PERSON|general", "canonical_name": "Alice", "chunk_id": "update:s1:7",
        "doc_id": "update:s1:7", "source_kind": "user_chat"}


def _stage_chat(folder):
    folder.mkdir(parents=True)
    (folder / "document.json").write_text(json.dumps({
        "id": "update:s1:7", "source_kind": "user_chat", "_domain": "general",
        "raw_text": "Alice is the CEO.", "session_id": "s1",
    }))
    (folder / "chunks.json").write_text("[]")
    (folder / "observations.json").write_text(json.dumps([_OBS]))
    (folder / "relationships.json").write_text("[]")
    return folder


def test_an_update_folder_commits_as_a_user_chat_not_a_document(tmp_path, session):
    folder = _stage_chat(tmp_path / "update__s1__7")

    ing._write_to_neo4j(folder, "general", defer_rebuild=True)

    chats = session.matching("CREATE (n:UserChat {id: $id})")
    assert len(chats) == 1
    assert chats[0][1]["id"] == "update:s1:7"
    assert chats[0][1]["props"]["raw_text"] == "Alice is the CEO."
    assert "REMOVE n:UserChatHistory" in chats[0][0], "a retracted chat is revived, not duplicated"
    assert session.matching("CREATE (n:Document {id: $id})") == []


def test_an_update_folders_fingerprint_is_recorded_on_its_user_chat(tmp_path, session):
    folder = _stage_chat(tmp_path / "update__s1__7")

    ing._write_to_neo4j(folder, "general", defer_rebuild=True)

    assert [p for c, p in session.matching("SET c.fingerprint = $fingerprint")] == [
        {"doc_id": "update:s1:7", "fingerprint": sync_state.folder_fingerprint(folder)}
    ]
    assert session.matching("SET d.fingerprint") == []


def test_an_update_folders_observations_are_extracted_from_its_user_chat(tmp_path, session):
    folder = _stage_chat(tmp_path / "update__s1__7")

    ing._write_to_neo4j(folder, "general", defer_rebuild=True)

    links = session.matching("MERGE (o)-[:EXTRACTED_FROM]->(c)")
    assert [(("UserChat" in c), p) for c, p in links] == [(True, {"id": "o1", "doc_id": "update:s1:7"})]


def test_a_document_still_commits_as_a_document(tmp_path, session):
    folder = tmp_path / "doc"
    folder.mkdir()
    (folder / "document.json").write_text(json.dumps({"id": "doc-1", "name": "a.md"}))
    (folder / "observations.json").write_text("[]")

    ing._write_to_neo4j(folder, "general", defer_rebuild=True)

    assert len(session.matching("CREATE (n:Document {id: $id})")) == 1
    assert session.matching("UserChat") == []


def test_retract_document_also_retires_a_user_chat(session, monkeypatch):
    import artmind.projection as projection

    monkeypatch.setattr(projection, "keys_for_document", lambda tx, doc_id, **kw: set())

    ing.retract_document("update:s1:7", "general")

    assert session.matching("REMOVE c:UserChat SET c:UserChatHistory") == [
        ("MATCH (c:UserChat {id: $doc_id}) REMOVE c:UserChat SET c:UserChatHistory", {"doc_id": "update:s1:7"})
    ]


def test_fingerprints_are_read_from_documents_and_user_chats_in_one_query(monkeypatch):
    import artmind.graph_query as graph_query

    fake = _Session()

    @contextmanager
    def _fake(access_mode=None, *, timeout=None):
        yield fake

    monkeypatch.setattr(graph_query, "neo4j_session", _fake)

    sync_state.read_document_fingerprints(["update:s1:7", "doc-1"])

    (cypher, params), = fake.calls
    assert "MATCH (d:Document) WHERE d.id IN $doc_ids" in cypher
    assert "UNION ALL" in cypher and "MATCH (c:UserChat) WHERE c.id IN $doc_ids" in cypher
    assert params == {"doc_ids": ["doc-1", "update:s1:7"]}


def test_a_fingerprint_goes_on_a_document_or_a_user_chat_only():
    with pytest.raises(ValueError, match="only Document or UserChat"):
        sync_state.set_document_fingerprint(_Session(), "x", "fp", label="Entity")


def test_setup_constrains_the_user_chat_history_id():
    from artmind.setup import _setup_neo4j

    fake = _Session()
    _setup_neo4j(fake, 768)

    assert fake.matching("CREATE CONSTRAINT user_chat_history_id IF NOT EXISTS FOR (n:UserChatHistory) REQUIRE n.id IS UNIQUE")
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --group dev pytest test/test_user_chat_commit.py test/test_graph_snapshot.py -q`
Expected: `8 failed, 43 passed` — `AssertionError` for the missing `:UserChat` merge, fingerprint, `EXTRACTED_FROM` link, relabel, `UNION ALL` and `user_chat_history_id` constraint, `TypeError: set_document_fingerprint() got an unexpected keyword argument 'label'`, and the snapshot label test (`UserChatHistory` missing from `BASE_LABELS`).

- [ ] **Step 3: Implement**

**Replace in** `artmind/graph_snapshot.py` (lines 28-34):

```python
BASE_LABELS = (
    "Document", "DocumentHistory",
    "DocChunk", "DocChunkHistory",
    "UserChat",
    "Observation", "ObservationHistory",
    "Synthesis",
)
```

**with:**

```python
BASE_LABELS = (
    "Document", "DocumentHistory",
    "DocChunk", "DocChunkHistory",
    "UserChat", "UserChatHistory",
    "Observation", "ObservationHistory",
    "Synthesis",
)
```

**Replace in** `artmind/ingest.py` (lines 1680-1685):

```python
            "MATCH (d:Document {id: $doc_id}) REMOVE d:Document SET d:DocumentHistory",
            doc_id=doc_id,
        )
        return {"doc_id": doc_id, "domain": domain, "affected_keys": sorted(keys), **retracted}

    with neo4j_session() as session:
```

**with:**

```python
            "MATCH (d:Document {id: $doc_id}) REMOVE d:Document SET d:DocumentHistory",
            doc_id=doc_id,
        )
        # An `update__*` folder commits as a :UserChat (`_commit_document_tx`).
        tx.run(
            "MATCH (c:UserChat {id: $doc_id}) REMOVE c:UserChat SET c:UserChatHistory",
            doc_id=doc_id,
        )
        return {"doc_id": doc_id, "domain": domain, "affected_keys": sorted(keys), **retracted}

    with neo4j_session() as session:
```

**Replace in** `artmind/ingest.py` (lines 2517-2523):

```python
    )


def _write_observations(tx, observations: list[dict], doc_id: str) -> int:
    """Write this document version's observations.

    Immutable records: written whole, never merged into and never patched. The
```

**with:**

```python
    )


#: `document.json`'s `source_kind` for an `artmind update` staging folder
#: (`update.write_user_chat`). Committed as a `:UserChat`, not a `:Document`.
USER_CHAT_SOURCE_KIND = "user_chat"


def _write_observations(tx, observations: list[dict], doc_id: str, *, source_label: str = "DocChunk") -> int:
    """Write this document version's observations.

    Immutable records: written whole, never merged into and never patched. The
```

**Replace in** `artmind/ingest.py` (lines 2529-2539):

```python
    Each observation is a complete statement of what one chunk said, replaced
    whole (never `+=`) — a `+=` would let a property from a previous version
    of the same chunk survive into this one.
    """
    for observation in observations:
        _merge_relabeled(tx, "Observation", "ObservationHistory", observation["id"], observation)
        chunk_id = observation.get("chunk_id")
        if chunk_id:
            tx.run(
                """
                MATCH (o:Observation {id: $id})
```

**with:**

```python
    Each observation is a complete statement of what one chunk said, replaced
    whole (never `+=`) — a `+=` would let a property from a previous version
    of the same chunk survive into this one.

    `source_label="UserChat"` (an `artmind update`): every observation is
    `EXTRACTED_FROM` the `:UserChat` whose id is `doc_id`, not a chunk.
    """
    for observation in observations:
        _merge_relabeled(tx, "Observation", "ObservationHistory", observation["id"], observation)
        chunk_id = observation.get("chunk_id")
        if source_label == "UserChat":
            tx.run(
                """
                MATCH (o:Observation {id: $id})
                MATCH (c:UserChat {id: $doc_id})
                MERGE (o)-[:EXTRACTED_FROM]->(c)
                """,
                id=observation["id"], doc_id=doc_id,
            )
        elif chunk_id:
            tx.run(
                """
                MATCH (o:Observation {id: $id})
```

**Replace in** `artmind/ingest.py` (lines 2713-2718):

```python
    and logged a warning, which meant a broken projection looked exactly like
    a healthy one from the outside. A silently-skipped projection is a
    silently-stale query layer.
    """
    from artmind import projection, same_as, sync_state
    from artmind.observations import key_string
```

**with:**

```python
    and logged a warning, which meant a broken projection looked exactly like
    a healthy one from the outside. A silently-skipped projection is a
    silently-stale query layer.

    An `artmind update` staging folder (`document.json`'s `source_kind` is
    `USER_CHAT_SOURCE_KIND`) commits the same way, with one difference: its
    provenance node is a `:UserChat` (the user's own words), not a
    `:Document` with chunks, and its observations are `EXTRACTED_FROM` it.
    `update confirm` and `vault sync`'s replay both reach this function with
    the same folder, so the two write the same graph (spec 2026-09-26 §7).
    """
    from artmind import projection, same_as, sync_state
    from artmind.observations import key_string
```

**Replace in** `artmind/ingest.py` (lines 2720-2725):

```python
    document = staged["document"]
    domain = staged["domain"]
    doc_id = document["id"]
    summary: dict = {}

    # 1. The prior version's keys — set 2 of the affected-key union. Captured
```

**with:**

```python
    document = staged["document"]
    domain = staged["domain"]
    doc_id = document["id"]
    source_label = "UserChat" if document.get("source_kind") == USER_CHAT_SOURCE_KIND else "Document"
    summary: dict = {}

    # 1. The prior version's keys — set 2 of the affected-key union. Captured
```

**Replace in** `artmind/ingest.py` (lines 2733-2746):

```python
    # 3. Document + chunks. A re-ingest of a document `docs retire` had moved
    #    to :DocumentHistory must find and revive that same node, not create a
    #    duplicate under :Document — see _merge_relabeled.
    _merge_relabeled(tx, "Document", "DocumentHistory", doc_id, _flatten_props(document), replace=False)
    # 3b. The staging folder's fingerprint (spec 2026-09-26 §6 A3): `vault
    #     sync` skips a committed folder whose fingerprint the graph already
    #     carries -- on a shared graph, the machine that ingested it wrote it.
    #     Every path that commits a staged folder (ingest, table2graph,
    #     write-to-graph, pull-kg, archive restore, dashboard import, vault
    #     sync) reaches this line through `_write_to_neo4j`/`_load_staged`.
    sync_state.set_document_fingerprint(tx, doc_id, staged.get("fingerprint"))
    for chunk in staged["chunks"]:
        # `embedding` flows through `_flatten_props` like every other chunk
        # property (no more special-casing it out and re-adding it after).
```

**with:**

```python
    # 3. Document + chunks. A re-ingest of a document `docs retire` had moved
    #    to :DocumentHistory must find and revive that same node, not create a
    #    duplicate under :Document — see _merge_relabeled.
    _merge_relabeled(tx, source_label, f"{source_label}History", doc_id, _flatten_props(document), replace=False)
    # 3b. The staging folder's fingerprint (spec 2026-09-26 §6 A3): `vault
    #     sync` skips a committed folder whose fingerprint the graph already
    #     carries -- on a shared graph, the machine that ingested it wrote it.
    #     Every path that commits a staged folder (ingest, table2graph,
    #     write-to-graph, pull-kg, archive restore, dashboard import, vault
    #     sync) reaches this line through `_write_to_neo4j`/`_load_staged`.
    sync_state.set_document_fingerprint(tx, doc_id, staged.get("fingerprint"), label=source_label)
    for chunk in staged["chunks"]:
        # `embedding` flows through `_flatten_props` like every other chunk
        # property (no more special-casing it out and re-adding it after).
```

**Replace in** `artmind/ingest.py` (lines 2779-2785):

```python

    # 4. Observations.
    observations = staged["observations"]
    summary["observations"] = _write_observations(tx, observations, doc_id)

    incoming_keys = {
        (o["key"].split("|")[0], o["key"].split("|")[1], o["key"].split("|")[2])
```

**with:**

```python

    # 4. Observations.
    observations = staged["observations"]
    if source_label == "UserChat":
        summary["observations"] = _write_observations(tx, observations, doc_id, source_label="UserChat")
    else:
        summary["observations"] = _write_observations(tx, observations, doc_id)

    incoming_keys = {
        (o["key"].split("|")[0], o["key"].split("|")[1], o["key"].split("|")[2])
```

**Replace in** `artmind/setup.py` (lines 540-545):

```python
    session.run(
        "CREATE CONSTRAINT observation_history_id IF NOT EXISTS FOR (n:ObservationHistory) REQUIRE n.id IS UNIQUE"
    )
    session.run(
        "CREATE INDEX document_history_domain IF NOT EXISTS FOR (n:DocumentHistory) ON (n._domain)"
    )
```

**with:**

```python
    session.run(
        "CREATE CONSTRAINT observation_history_id IF NOT EXISTS FOR (n:ObservationHistory) REQUIRE n.id IS UNIQUE"
    )
    # A retracted `artmind update` (its `update__*` staging folder deleted):
    # the UserChat keeps its id under the History label, like a Document.
    session.run(
        "CREATE CONSTRAINT user_chat_history_id IF NOT EXISTS FOR (n:UserChatHistory) REQUIRE n.id IS UNIQUE"
    )
    session.run(
        "CREATE INDEX document_history_domain IF NOT EXISTS FOR (n:DocumentHistory) ON (n._domain)"
    )
```

**Replace in** `artmind/setup.py` (lines 862-867):

```python
            "document_history_id",
            "chunk_history_id",
            "observation_history_id",
            "observation_id",
            neo4j_notes["entity_id_schema"],
            "cat_table_key",
```

**with:**

```python
            "document_history_id",
            "chunk_history_id",
            "observation_history_id",
            "user_chat_history_id",
            "observation_id",
            neo4j_notes["entity_id_schema"],
            "cat_table_key",
```

**Replace in** `artmind/sync_state.py` (lines 46-54):

```python
    "SET s.last_applied_commit = $commit, s.applied_at = $applied_at"
)
_SET_FINGERPRINT = "MATCH (d:Document {id: $doc_id}) SET d.fingerprint = $fingerprint"
_READ_FINGERPRINTS = (
    "MATCH (d:Document) WHERE d.id IN $doc_ids "
    "RETURN d.id AS id, d.fingerprint AS fingerprint"
)


```

**with:**

```python
    "SET s.last_applied_commit = $commit, s.applied_at = $applied_at"
)
_SET_FINGERPRINT = "MATCH (d:Document {id: $doc_id}) SET d.fingerprint = $fingerprint"
#: An `artmind update` staging folder (`update__*`) is committed as a
#: `:UserChat`, not a `:Document` (`ingest._commit_document_tx`), and carries
#: its fingerprint there.
_SET_USER_CHAT_FINGERPRINT = "MATCH (c:UserChat {id: $doc_id}) SET c.fingerprint = $fingerprint"
_READ_FINGERPRINTS = (
    "MATCH (d:Document) WHERE d.id IN $doc_ids "
    "RETURN d.id AS id, d.fingerprint AS fingerprint "
    "UNION ALL "
    "MATCH (c:UserChat) WHERE c.id IN $doc_ids "
    "RETURN c.id AS id, c.fingerprint AS fingerprint"
)


```

**Replace in** `artmind/sync_state.py` (lines 89-99):

```python
    return staging_fingerprint(_read)


def set_document_fingerprint(tx, doc_id: str, fingerprint: str | None) -> None:
    """Record `fingerprint` on `(:Document {id: doc_id})`, inside the commit
    transaction (`ingest._commit_document_tx`). None removes the property, so
    a document committed without one can never be skipped by `vault sync`."""
    tx.run(_SET_FINGERPRINT, doc_id=doc_id, fingerprint=fingerprint)


def _session(timeout: float | None):
```

**with:**

```python
    return staging_fingerprint(_read)


def set_document_fingerprint(tx, doc_id: str, fingerprint: str | None, *, label: str = "Document") -> None:
    """Record `fingerprint` on `(:Document {id: doc_id})` -- or, with
    `label="UserChat"`, on the `:UserChat` an update folder commits as --
    inside the commit transaction (`ingest._commit_document_tx`). None
    removes the property, so a document committed without one can never be
    skipped by `vault sync`."""
    if label not in ("Document", "UserChat"):
        raise ValueError(f"no fingerprint on {label!r}: only Document or UserChat")
    tx.run(_SET_FINGERPRINT if label == "Document" else _SET_USER_CHAT_FINGERPRINT, doc_id=doc_id, fingerprint=fingerprint)


def _session(timeout: float | None):
```

**Replace in** `artmind/sync_state.py` (lines 104-112):

```python

def read_document_fingerprints(doc_ids: list[str], *, timeout: float | None = None) -> dict[str, str | None]:
    """`{doc_id: fingerprint}` for every id in `doc_ids` that is a live
    `:Document` (not `:DocumentHistory`), in ONE query. A document the graph
    does not have is absent from the result; one committed before fingerprints
    existed maps to None."""
    if not doc_ids:
        return {}
    with _session(timeout) as session:
```

**with:**

```python

def read_document_fingerprints(doc_ids: list[str], *, timeout: float | None = None) -> dict[str, str | None]:
    """`{doc_id: fingerprint}` for every id in `doc_ids` that is a live
    `:Document` or `:UserChat` (not a History label), in ONE query. A
    document the graph does not have is absent from the result; one committed
    before fingerprints existed maps to None."""
    if not doc_ids:
        return {}
    with _session(timeout) as session:
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --group dev pytest test/test_user_chat_commit.py test/test_graph_snapshot.py -q`
Expected: `51 passed`. Then the full suite — all pass.

- [ ] **Step 5: Commit**

```bash
git add artmind/graph_snapshot.py artmind/ingest.py artmind/setup.py artmind/sync_state.py test/test_graph_snapshot.py test/test_user_chat_commit.py
git commit -m "feat(ingest): an update staging folder commits as a UserChat through _commit_document_tx"
```

---

### Task 7: `update confirm` writes the staging folder and commits the graph from it; `vault sync` replays it

`write_user_chat` keeps its resolution loop (it needs this machine's graph to find the node a user linked to, and records that node's identity in each observation). Then, instead of its own Cypher, it writes `kg/<domain>/update__<session_id>__<draft_id>/` with `ingest.write_staging` (atomic, spec R2), reads the folder back with `ingest._load_staged`, and commits it with `ingest._commit_document_tx` in the same session. Relationships are staged naming their endpoint observations by id (`source_observation_id`/`target_observation_id`), chunk- and doc-scoped to the chat, so the edge id is the one the old writer used. The chat's vector is derived and never staged: `confirm` sets it after the commit, and `vault sync` embeds replayed chats with the new `update.embed_user_chats`.

The replay-parity test runs `confirm`, then replays the folder with `ingest._write_to_neo4j`, and compares the two commit transactions statement by statement.

Five `test_update.py` tests filtered observation writes on `MERGE (o:Observation`; the commit writes them with `_merge_relabeled` (`CREATE (n:Observation {id: $id})`), so the filters change, and `test_a_chat_never_writes_entity_properties_directly` now checks that the chat commits through `_commit_document_tx`.

**Files:**
- Modify: `artmind/ingest.py`
- Modify: `artmind/update.py`
- Modify: `artmind/vault_sync.py`
- Test: `test/test_update.py`
- Test: `test/test_update_staging.py` (new)
- Test: `test/test_vault_sync.py`

- [ ] **Step 1: Write the failing tests**

**Replace in** `test/test_update.py` (lines 525-531):

```python
    assert result["nodes_updated"] == 1
    assert not [c for c, _ in calls if "CREATE (e:" in c]
    # One observation, keyed so the rebuild lands it on the existing entity.
    written = [kw["props"] for c, kw in calls if "MERGE (o:Observation" in c]
    assert len(written) == 1
    assert written[0]["canonical_name"] == "Alice"

```

**with:**

```python
    assert result["nodes_updated"] == 1
    assert not [c for c, _ in calls if "CREATE (e:" in c]
    # One observation, keyed so the rebuild lands it on the existing entity.
    written = [kw["props"] for c, kw in calls if "CREATE (n:Observation {id: $id})" in c]
    assert len(written) == 1
    assert written[0]["canonical_name"] == "Alice"

```

**Replace in** `test/test_update.py` (lines 764-770):

```python
        )

    assert result["observations_written"] == 1
    written = [kw["props"] for c, kw in calls if "MERGE (o:Observation" in c]
    assert len(written) == 1
    observation = written[0]

```

**with:**

```python
        )

    assert result["observations_written"] == 1
    written = [kw["props"] for c, kw in calls if "CREATE (n:Observation {id: $id})" in c]
    assert len(written) == 1
    observation = written[0]

```

**Replace in** `test/test_update.py` (lines 813-819):

```python
            extracted_entities=extracted_entities, extracted_relationships=[],
        )

    written = [kw["props"] for c, kw in calls if "MERGE (o:Observation" in c]
    assert len(written) == 1
    assert written[0]["key"] == stored_key

```

**with:**

```python
            extracted_entities=extracted_entities, extracted_relationships=[],
        )

    written = [kw["props"] for c, kw in calls if "CREATE (n:Observation {id: $id})" in c]
    assert len(written) == 1
    assert written[0]["key"] == stored_key

```

**Replace in** `test/test_update.py` (lines 821-827):

```python
def test_a_chat_never_writes_entity_properties_directly():
    """The projection owns every Entity property. A direct write would be
    silently reverted by the next rebuild — which is exactly why this path was
    retargeted rather than left alone."""
    import inspect

    from artmind.update import write_user_chat as fn
```

**with:**

```python
def test_a_chat_never_writes_entity_properties_directly():
    """The projection owns every Entity property. A direct write would be
    silently reverted by the next rebuild — which is exactly why this path was
    retargeted rather than left alone. It now commits through the document
    commit transaction, the same one `vault sync` replays the folder with."""
    import inspect

    from artmind.update import write_user_chat as fn
```

**Replace in** `test/test_update.py` (lines 829-835):

```python
    src = inspect.getsource(fn)
    assert "SET e +=" not in src
    assert "CREATE (e:" not in src
    assert "MERGE (o:Observation" in src


def test_retraction_writes_a_thin_observation_carrying__retracts():
```

**with:**

```python
    src = inspect.getsource(fn)
    assert "SET e +=" not in src
    assert "CREATE (e:" not in src
    assert "_commit_document_tx" in src


def test_retraction_writes_a_thin_observation_carrying__retracts():
```

**Replace in** `test/test_update.py` (lines 881-887):

```python

    observation_writes = [
        kwargs["props"] for cypher, kwargs in calls
        if cypher.strip().startswith("MERGE (o:Observation") and "props" in kwargs
    ]
    retracting = [p for p in observation_writes if p.get("_retracts")]
    assert len(retracting) == 1
```

**with:**

```python

    observation_writes = [
        kwargs["props"] for cypher, kwargs in calls
        if "CREATE (n:Observation {id: $id})" in cypher and "props" in kwargs
    ]
    retracting = [p for p in observation_writes if p.get("_retracts")]
    assert len(retracting) == 1
```

**Create** `test/test_update_staging.py`:

```python
"""`artmind update confirm` travels (spec 2026-09-26 §7, D6): it writes a
staging folder, `kg/<domain>/update__<session_id>__<draft_id>/`, and commits
the graph FROM that folder through `ingest._commit_document_tx` -- the same
transaction `vault sync` replays the folder with on another machine. No
Neo4j: recording sessions stand in, and the tests compare the Cypher and
parameters actually sent (CLAUDE.md §3)."""
import json
from contextlib import contextmanager

import pytest

import artmind.ingest as ing
import artmind.update as upd
from artmind import sync_state


class _Result:
    def __init__(self, rows=()):
        self._rows = list(rows)

    def single(self):
        return self._rows[0] if self._rows else None

    def data(self):
        return list(self._rows)

    def __iter__(self):
        return iter(self._rows)


class _Session:
    """Records every statement; those sent inside `execute_write` also go to
    `tx_calls` -- the commit transaction, which is what must be identical
    between `confirm` and a replay."""

    def __init__(self):
        self.calls = []
        self.tx_calls = []
        self._in_tx = False

    def run(self, cypher, **params):
        self.calls.append((cypher, params))
        if self._in_tx:
            self.tx_calls.append((cypher, params))
        return _Result()

    def execute_write(self, fn, *args, **kwargs):
        self._in_tx = True
        try:
            return fn(self, *args, **kwargs)
        finally:
            self._in_tx = False


@pytest.fixture()
def kg_dir(tmp_path, monkeypatch):
    import paths

    target = tmp_path / ".artmind" / "data" / "kg"
    monkeypatch.setattr(paths, "KG_DIR", target)
    return target


@pytest.fixture()
def rebuilds(monkeypatch):
    import artmind.projection as projection

    seen = []
    monkeypatch.setattr(projection, "rebuild", lambda tx, keys, **kw: seen.append(sorted(keys)) or {"keys": len(keys)})
    return seen


def _confirm(monkeypatch, *, draft_id=7, relationships=True):
    session = _Session()

    @contextmanager
    def _fake():
        yield session

    monkeypatch.setattr(upd, "neo4j_session", _fake)
    monkeypatch.setattr(upd, "embed_text", lambda model, text: [0.25, 0.5])
    result = upd.write_user_chat(
        session_id="s1", raw_text="Alice works at Acme.", domain="general", user_id="u@example.com",
        resolutions=[
            {"entity_temp_id": "e0", "action": "create", "node_id": None},
            {"entity_temp_id": "e1", "action": "create", "node_id": None},
        ],
        extracted_entities=[
            {"temp_id": "e0", "name": "Alice", "entity_class": "PERSON", "properties": {"role": "CEO"}},
            {"temp_id": "e1", "name": "Acme", "entity_class": "ORGANIZATION", "properties": {}},
        ],
        extracted_relationships=[{"source_temp_id": "e0", "target_temp_id": "e1", "rel_type": "works_at"}] if relationships else [],
        draft_id=draft_id,
    )
    return session, result


def test_confirm_writes_one_staging_folder_per_confirmed_draft(kg_dir, monkeypatch, rebuilds):
    _, result = _confirm(monkeypatch)

    folder = kg_dir / "general" / "update__s1__7"
    assert result["staging_dir"] == str(folder)
    assert sorted(p.name for p in (kg_dir / "general").iterdir()) == ["update__s1__7"], "no atomic-write scratch left"
    document = json.loads((folder / "document.json").read_text())
    assert document["id"] == result["user_chat_id"] == "update:s1:7"
    assert (document["source_kind"], document["raw_text"], document["session_id"]) == ("user_chat", "Alice works at Acme.", "s1")
    assert "embedding" not in document, "a vector is derived, never staged"
    observations = json.loads((folder / "observations.json").read_text())
    assert sorted(o["canonical_name"] for o in observations) == ["Acme", "Alice"]
    assert all(o["doc_id"] == "update:s1:7" for o in observations)
    (relationship,) = json.loads((folder / "relationships.json").read_text())
    by_name = {o["canonical_name"]: o["id"] for o in observations}
    assert relationship == {
        "source_observation_id": by_name["Alice"], "target_observation_id": by_name["Acme"],
        "source_name": "Alice", "target_name": "Acme", "rel_type": "WORKS_AT",
        "chunk_id": "update:s1:7", "doc_id": "update:s1:7",
    }


def test_confirm_commits_from_the_folder_and_records_its_fingerprint(kg_dir, monkeypatch, rebuilds):
    session, result = _confirm(monkeypatch)

    folder = kg_dir / "general" / "update__s1__7"
    assert [p for c, p in session.tx_calls if "SET c.fingerprint" in c] == [
        {"doc_id": "update:s1:7", "fingerprint": sync_state.folder_fingerprint(folder)}
    ]
    assert result["relationships_written"] == 1
    assert [p for c, p in session.calls if c == "MATCH (c:UserChat {id: $id}) SET c.embedding = $embedding"] == [
        {"id": "update:s1:7", "embedding": [0.25, 0.5]}
    ]


def test_replaying_the_folder_writes_exactly_what_confirm_wrote(kg_dir, monkeypatch, rebuilds):
    """What another machine's `vault sync` does with the committed folder:
    the same commit transaction, statement for statement, parameter for
    parameter."""
    confirm_session, _ = _confirm(monkeypatch)

    import artmind.graph_query as graph_query

    replay_session = _Session()

    @contextmanager
    def _fake(*a, **k):
        yield replay_session

    monkeypatch.setattr(graph_query, "neo4j_session", _fake)
    monkeypatch.setattr(ing, "_ensure_neo4j_schema", lambda *a, **k: None)

    ing._write_to_neo4j(kg_dir / "general" / "update__s1__7", "general")

    assert replay_session.tx_calls == confirm_session.tx_calls
    assert any("ASSERTS_RELATION" in c for c, _ in replay_session.tx_calls)
    assert rebuilds[0] == rebuilds[1]


def test_a_retried_confirm_of_the_same_draft_rewrites_the_same_folder(kg_dir, monkeypatch, rebuilds):
    first, a = _confirm(monkeypatch)
    second, b = _confirm(monkeypatch)

    assert a["user_chat_id"] == b["user_chat_id"]
    assert [p.name for p in (kg_dir / "general").iterdir()] == ["update__s1__7"]

    def _observation_ids(session):
        return sorted(p["id"] for c, p in session.tx_calls if "CREATE (n:Observation {id: $id})" in c)

    assert _observation_ids(first) == _observation_ids(second) and len(_observation_ids(first)) == 2


def test_confirm_update_names_the_folder_after_the_draft(monkeypatch):
    seen = {}
    monkeypatch.setattr(upd, "_get_latest_pending_draft", lambda sid: {
        "id": 42, "session_id": sid, "raw_text": "x", "extraction_json": json.dumps({"entities": [], "relationships": []}),
        "domain": "general",
    })
    monkeypatch.setattr(upd, "write_user_chat", lambda **kw: seen.update(kw) or {"ok": True})
    monkeypatch.setattr(upd, "_update_draft_status", lambda *a: None)
    monkeypatch.setattr(upd, "_update_session_status", lambda *a: None)

    upd.confirm_update("s1", [], "u")

    assert seen["draft_id"] == 42


def test_user_chat_ids_are_deterministic_and_session_scoped():
    assert upd.user_chat_id("s1", 7) == "update:s1:7"
    assert upd.update_folder("general", "s1", 7).parts[-2:] == ("general", "update__s1__7")


def test_embed_user_chats_embeds_only_chats_without_a_vector(monkeypatch):
    class _Chats(_Session):
        def run(self, cypher, **params):
            super().run(cypher, **params)
            if "c.embedding IS NULL" in cypher:
                return _Result([{"id": "update:s1:7", "raw_text": "Alice works at Acme."}])
            return _Result()

    session = _Chats()

    @contextmanager
    def _fake():
        yield session

    monkeypatch.setattr(upd, "neo4j_session", _fake)
    monkeypatch.setattr(upd, "embed_text", lambda model, text: [0.1] if text == "Alice works at Acme." else None)

    assert upd.embed_user_chats(domain="general") == 1
    read, write = session.calls
    assert read[1] == {"domain": "general", "ids": None}
    assert write == ("MATCH (c:UserChat {id: $id}) SET c.embedding = $embedding", {"id": "update:s1:7", "embedding": [0.1]})
```

**Replace in** `test/test_vault_sync.py` (lines 3432-3434):

```python
    with pytest.raises(vs.VaultSyncError, match="not a conflicts record for id 'c1'"):
        vs.sync(repo)
    assert _bookmarks(repo) == (None, None)
```

**with:**

```python
    with pytest.raises(vs.VaultSyncError, match="not a conflicts record for id 'c1'"):
        vs.sync(repo)
    assert _bookmarks(repo) == (None, None)


# ── `artmind update` folders replay like documents (spec 2026-09-26 §7) ──────


def _write_update_folder(kg_dir, domain, session_id, draft_id, raw_text="Alice is the CEO."):
    from artmind.ingest import write_staging

    chat_id = f"update:{session_id}:{draft_id}"
    folder = kg_dir / domain / f"update__{session_id}__{draft_id}"
    write_staging(folder, {
        "document.json": {"id": chat_id, "source_kind": "user_chat", "_domain": domain,
                          "raw_text": raw_text, "session_id": session_id},
        "chunks.json": [],
        "observations.json": [{"id": "o1", "key": "alice|PERSON|general", "doc_id": chat_id, "chunk_id": chat_id}],
        "relationships.json": [],
    })
    return folder, chat_id


def test_sync_replays_an_update_folder_as_a_user_chat_and_embeds_it(repo, monkeypatch, curation_graph):
    import artmind.ingest as ing
    import artmind.update as upd

    real_write = ing._write_to_neo4j
    kg_dir = _patch_kg_dir(monkeypatch, repo)
    _patch_structured_text_dir(monkeypatch, repo)
    folder, chat_id = _write_update_folder(kg_dir, "general", "s1", 7)
    _commit_all(repo, "an update confirmed on the other machine")
    _write_update_folder(kg_dir, "general", "s1", 7, raw_text="an uncommitted edit")
    _patch_ingest_and_projection(monkeypatch)
    monkeypatch.setattr(ing, "_write_to_neo4j", real_write)
    monkeypatch.setattr(ing, "_ensure_neo4j_schema", lambda *a, **k: None)
    embedded = []
    monkeypatch.setattr(upd, "embed_user_chats", lambda **kw: embedded.append(kw) or 0)

    vs.sync(repo, bootstrap_empty=True)

    chats = [p for c, p in curation_graph.calls if "CREATE (n:UserChat {id: $id})" in c]
    assert [(p["id"], p["props"]["raw_text"]) for p in chats] == [(chat_id, "Alice is the CEO.")]
    assert embedded == [{"domain": "general"}]


def test_a_shared_graph_that_already_has_the_update_does_not_replay_it(repo, monkeypatch, graph):
    import artmind.update as upd
    from artmind import sync_state

    kg_dir = _patch_kg_dir(monkeypatch, repo)
    _patch_structured_text_dir(monkeypatch, repo)
    folder, chat_id = _write_update_folder(kg_dir, "general", "s1", 7)
    _commit_all(repo, "an update the other machine already wrote to the shared graph")
    graph.fingerprints[chat_id] = sync_state.folder_fingerprint(folder)
    calls = _patch_ingest_and_projection(monkeypatch)
    embedded = []
    monkeypatch.setattr(upd, "embed_user_chats", lambda **kw: embedded.append(kw) or 0)

    result = vs.sync(repo, bootstrap_empty=True)

    assert calls["write_to_neo4j"] == []
    assert result["unchanged"] == 1
    assert embedded == []


def test_a_deleted_update_folder_retracts_its_user_chat(repo, monkeypatch):
    import shutil

    kg_dir = _patch_kg_dir(monkeypatch, repo)
    _patch_structured_text_dir(monkeypatch, repo)
    folder, chat_id = _write_update_folder(kg_dir, "general", "s1", 7)
    _commit_all(repo, "an update")
    base = vs.head_sha(repo)
    shutil.rmtree(folder)
    _commit_all(repo, "the update retracted")
    from artmind.vault import VaultLayout, write_state
    write_state(VaultLayout(repo), {"last_synced_commit": base})
    calls = _patch_ingest_and_projection(monkeypatch)

    vs.sync(repo)

    assert calls["retract_document"] == [(chat_id, "general")]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --group dev pytest test/test_update.py test/test_update_staging.py test/test_vault_sync.py -q`
Expected: `14 failed, 189 passed` — four retargeted `test_update.py` tests find no `CREATE (n:Observation {id: $id})` write (`assert 0 == 1`) and the fifth finds no `_commit_document_tx` in `write_user_chat`; the seven `test_update_staging.py` tests fail with `TypeError: write_user_chat() got an unexpected keyword argument 'draft_id'`, `KeyError: 'draft_id'`, or `AttributeError` (`user_chat_id`, `embed_user_chats`); the two new sync tests fail on `monkeypatch.setattr(upd, "embed_user_chats", ...)` (`AttributeError`: no such attribute yet). `test_a_deleted_update_folder_retracts_its_user_chat` already passes: a deleted folder is retracted by track A's existing path — it pins that.

- [ ] **Step 3: Implement**

**Replace in** `artmind/ingest.py` (lines 2525-2530):

```python
#: `document.json`'s `source_kind` for an `artmind update` staging folder
#: (`update.write_user_chat`). Committed as a `:UserChat`, not a `:Document`.
USER_CHAT_SOURCE_KIND = "user_chat"


def _write_observations(tx, observations: list[dict], doc_id: str, *, source_label: str = "DocChunk") -> int:
```

**with:**

```python
#: `document.json`'s `source_kind` for an `artmind update` staging folder
#: (`update.write_user_chat`). Committed as a `:UserChat`, not a `:Document`.
USER_CHAT_SOURCE_KIND = "user_chat"
#: The folder-name prefix of an `artmind update` staging folder,
#: `kg/<domain>/update__<session_id>__<draft_id>/`.
UPDATE_FOLDER_PREFIX = "update__"


def _write_observations(tx, observations: list[dict], doc_id: str, *, source_label: str = "DocChunk") -> int:
```

**Replace in** `artmind/update.py` (lines 26-33):

```python
from artmind.graph_query import neo4j_session
from artmind.ingest import (
    RESERVED_REL_TYPES,
    _sanitize_label,
    embed_missing_entity_embeddings,
)
from paths import DOMAIN_SCHEMAS_DIR
from utils.functions import load_env, resolve_llm_model
```

**with:**

```python
from artmind.graph_query import neo4j_session
from artmind.ingest import (
    RESERVED_REL_TYPES,
    UPDATE_FOLDER_PREFIX,
    USER_CHAT_SOURCE_KIND,
    _commit_document_tx,
    _load_staged,
    _sanitize_label,
    embed_missing_entity_embeddings,
    write_staging,
)
from paths import DOMAIN_SCHEMAS_DIR
from utils.functions import load_env, resolve_llm_model
```

**Replace in** `artmind/update.py` (lines 260-265):

```python
    return None


def write_user_chat(
    session_id: str,
    raw_text: str,
```

**with:**

```python
    return None


def user_chat_id(session_id: str, draft_id: int | str) -> str:
    """`update:<session_id>:<draft_id>` -- the UserChat's id and its staging
    folder's document id.

    Deterministic per confirmed draft, so a retried `confirm` of the same
    draft rewrites the same folder and MERGEs onto the same nodes rather than
    duplicating the fact. Unique across machines: `session_id` is a uuid4
    minted once by `update draft`, and `draft_id` is that draft's row id in
    the registry of the one machine the session lives on."""
    return f"update:{session_id}:{draft_id}"


def update_folder(domain: str, session_id: str, draft_id: int | str) -> Path:
    """`kg/<domain>/update__<session_id>__<draft_id>/` -- one staging folder
    per confirmed draft (a session may confirm several)."""
    import paths

    return Path(paths.KG_DIR) / domain / f"{UPDATE_FOLDER_PREFIX}{session_id}__{draft_id}"


def write_user_chat(
    session_id: str,
    raw_text: str,
```

**Replace in** `artmind/update.py` (lines 268-276):

```python
    resolutions: list[dict],
    extracted_entities: list[dict],
    extracted_relationships: list[dict],
) -> dict:
    """Record a UserChat and the Observations it asserts, then rebuild.

    A conversational correction is a **source**, exactly like a document, and
    it earns its authority the same way every other source does: its
    observations carry today's date as their document-level `valid_from`, so
```

**with:**

```python
    resolutions: list[dict],
    extracted_entities: list[dict],
    extracted_relationships: list[dict],
    *,
    draft_id: int | str | None = None,
) -> dict:
    """Record a UserChat and the Observations it asserts, then rebuild.

    **The graph must be rebuildable from the vault (spec 2026-09-26 §7, D6).**
    The chat is written as a staging folder,
    `kg/<domain>/update__<session_id>__<draft_id>/` (`document.json` holding
    the UserChat, `observations.json`, `relationships.json`), atomically, and
    then committed from that folder through `ingest._commit_document_tx` --
    exactly what `vault sync` does with the same folder on another machine.
    One code path, so the replay produces the graph this write did. The
    fingerprint the commit records lets a shared graph's `vault sync` skip it.

    A conversational correction is a **source**, exactly like a document, and
    it earns its authority the same way every other source does: its
    observations carry today's date as their document-level `valid_from`, so
```

**Replace in** `artmind/update.py` (lines 288-294):

```python
    rebuilds, never mutates the retracted observation — see
    `projection.apply_retractions`.
    """
    from artmind import projection
    from artmind.observations import aggregate_key, build_observation, key_string
    from artmind.temporal import load_schema

```

**with:**

```python
    rebuilds, never mutates the retracted observation — see
    `projection.apply_retractions`.
    """
    from artmind.observations import aggregate_key, build_observation, key_string
    from artmind.temporal import load_schema

```

**Replace in** `artmind/update.py` (lines 297-303):

```python
    embedding_dim = int(env.get("ARTMIND_KG_EMBEDDING_DIMENSIONS", "768"))
    now = datetime.now().isoformat()
    today = date.today().isoformat()
    chat_id = uuid.uuid4().hex
    embedding = embed_text(embed_model, raw_text)
    input_hint = _classify_input(raw_text)
    schema = load_schema(domain)
```

**with:**

```python
    embedding_dim = int(env.get("ARTMIND_KG_EMBEDDING_DIMENSIONS", "768"))
    now = datetime.now().isoformat()
    today = date.today().isoformat()
    draft_part = draft_id if draft_id is not None else uuid.uuid4().hex[:12]
    chat_id = user_chat_id(session_id, draft_part)
    embedding = embed_text(embed_model, raw_text)
    input_hint = _classify_input(raw_text)
    schema = load_schema(domain)
```

**Replace in** `artmind/update.py` (lines 311-330):

```python
    with neo4j_session() as session:
        _ensure_user_chat_schema(session, embedding_dim)

        session.run(
            """
            CREATE (c:UserChat {
                id: $id, raw_text: $raw_text, embedding: $embedding,
                _domain: $domain, session_id: $session_id,
                input_hint: $input_hint, created_at: $now, created_by: $user_id,
                _status: 'latest', _valid_from: $today
            })
            """,
            id=chat_id, raw_text=raw_text, embedding=embedding,
            domain=domain, session_id=session_id, today=today,
            input_hint=input_hint, now=now, user_id=user_id,
        )

        for res in resolutions:
            temp_id = res["entity_temp_id"]
            action = res["action"]
```

**with:**

```python
    with neo4j_session() as session:
        _ensure_user_chat_schema(session, embedding_dim)

        for res in resolutions:
            temp_id = res["entity_temp_id"]
            action = res["action"]
```

**Replace in** `artmind/update.py` (lines 425-469):

```python
                observations.append(retraction)
                nodes_retracted += 1

        # One transaction: the observations, their relationship observations,
        # and the rebuild they dirty — all three, together, same as a document
        # commit. Relationships are written BEFORE the rebuild (not after, as
        # the pre-Phase-4 direct-to-Entity writer required): the rebuild's
        # relationship aggregation reads ASSERTS_RELATION, so it has to exist
        # first, and both endpoints are always in `keys` already (both are
        # entities this same chat just observed).
        def _write(tx):
            for observation in observations:
                tx.run(
                    "MERGE (o:Observation {id: $id}) SET o = $props",
                    id=observation["id"], props=observation,
                )
                tx.run(
                    """
                    MATCH (o:Observation {id: $id})
                    MATCH (c:UserChat {id: $chat_id})
                    MERGE (o)-[:EXTRACTED_FROM]->(c)
                    """,
                    id=observation["id"], chat_id=chat_id,
                )
            rel_written = _write_chat_relation_observations(
                tx, extracted_relationships, resolved, chat_id,
            )
            keys = {
                tuple(o["key"].split("|")) for o in observations
                if o.get("key") and o["key"].count("|") == 2
            }
            # Retractions run before the rebuild, same ordering as a document
            # commit's step 4b — the target's key may differ from anything
            # else this chat touched.
            keys |= projection.apply_retractions(tx, observations)
            return (
                projection.rebuild(tx, keys, synthesis_loader=lambda ks: projection.load_synthesis_batch(tx, ks)),
                rel_written,
            )

        _, rel_count = session.execute_write(_write)

        embed_missing_entity_embeddings(session, domain, embed_model)

    return {
```

**with:**

```python
                observations.append(retraction)
                nodes_retracted += 1

        # The vault first, then the graph FROM the vault: the folder is
        # written atomically (spec R2), read back exactly as `vault sync`
        # reads it, and committed in one transaction -- observations,
        # retractions, relationship observations and the rebuild they dirty,
        # same as a document commit (`ingest._commit_document_tx`).
        folder = update_folder(domain, session_id, draft_part)
        document = {
            "id": chat_id,
            "source_kind": USER_CHAT_SOURCE_KIND,
            "name": f"update {session_id[:8]} #{draft_part}",
            "_domain": domain,
            "session_id": session_id,
            "raw_text": raw_text,
            "input_hint": input_hint,
            "created_at": now,
            "created_by": user_id,
            "_status": "latest",
            "_valid_from": today,
        }
        write_staging(folder, {
            "document.json": document,
            "chunks.json": [],
            "observations.json": observations,
            "relationships.json": _chat_relationships(extracted_relationships, resolved, chat_id),
        })
        staged = _load_staged(folder, domain)
        if not staged or not staged.get("document"):
            raise RuntimeError(f"could not read the update's staging folder back from {folder}")
        summary = session.execute_write(_commit_document_tx, staged, False)

        # The vector is derived (text + local model), so it is never staged --
        # like a chunk's. This machine has it already; `vault sync` embeds
        # the chat on the others (`embed_user_chats`).
        if embedding:
            session.run(
                "MATCH (c:UserChat {id: $id}) SET c.embedding = $embedding",
                id=chat_id, embedding=embedding,
            )
        embed_missing_entity_embeddings(session, domain, embed_model)

    return {
```

**Replace in** `artmind/update.py` (lines 472-481):

```python
        "nodes_updated": nodes_updated,
        "nodes_retracted": nodes_retracted,
        "observations_written": len(observations),
        "relationships_written": rel_count,
    }


def _stored_identity(key: str | None) -> str | None:
    """The name part of an Entity's stored `name|class|domain` key, or None
    when it is absent or malformed (fall back to recomputing from the name)."""
```

**with:**

```python
        "nodes_updated": nodes_updated,
        "nodes_retracted": nodes_retracted,
        "observations_written": len(observations),
        "relationships_written": summary["relationships"],
        "staging_dir": str(folder),
    }


def embed_user_chats(*, domain: str | None = None, chat_ids: list[str] | None = None) -> int:
    """Embed every `:UserChat` with no vector yet -- in `domain`, or among
    `chat_ids` -- and return how many were embedded. `vault sync` runs this
    for the update folders it replayed: a chat's vector is never staged."""
    env = load_env()
    embed_model = env.get("ARTMIND_KG_EMBEDDINGS_MODEL", "nomic-embed-text:latest")
    embedded = 0
    with neo4j_session() as session:
        rows = session.run(
            """
            MATCH (c:UserChat)
            WHERE c.embedding IS NULL
              AND ($domain IS NULL OR c._domain = $domain)
              AND ($ids IS NULL OR c.id IN $ids)
            RETURN c.id AS id, c.raw_text AS raw_text
            """,
            domain=domain, ids=chat_ids,
        ).data()
        for row in rows:
            vector = embed_text(embed_model, row.get("raw_text") or "")
            if vector:
                session.run(
                    "MATCH (c:UserChat {id: $id}) SET c.embedding = $embedding",
                    id=row["id"], embedding=vector,
                )
                embedded += 1
    return embedded


def _stored_identity(key: str | None) -> str | None:
    """The name part of an Entity's stored `name|class|domain` key, or None
    when it is absent or malformed (fall back to recomputing from the name)."""
```

**Replace in** `artmind/update.py` (lines 520-538):

```python
    return out


def _write_chat_relation_observations(tx, extracted_relationships, resolved, chat_id) -> int:
    """The immutable `ASSERTS_RELATION` record of relationships a chat asserted,
    between the Observations this chat just wrote — never a direct Entity-Entity
    write (see `ingest._write_relation_observations`, the document-ingest
    analogue this mirrors). The projection rebuild aggregates these into
    `RELATES_TO` the same way it does for document-sourced relationships;
    nothing here has to know how they'll be aggregated.

    Runs inside the caller's transaction, before the rebuild.
    """
    from artmind.observations import relation_observation_id

    written = 0
    for rel in extracted_relationships:
        src = resolved.get(rel.get("source_temp_id", ""))
        tgt = resolved.get(rel.get("target_temp_id", ""))
```

**with:**

```python
    return out


def _chat_relationships(extracted_relationships, resolved, chat_id) -> list[dict]:
    """The relationships a chat asserted, in the staging shape
    `ingest._write_relation_observations` commits: between the Observations
    this chat records (named by id, so nothing is re-resolved by name on
    replay), chunk- and doc-scoped to the chat. That writer turns each into
    an immutable `ASSERTS_RELATION` edge with id
    `relation_observation_id(chat_id, source, rel_type, target)` -- never a
    direct Entity-Entity write.

    A relationship naming an unresolved endpoint, a self-loop, or a reserved
    rel_type is dropped here, with the reserved case logged.
    """
    staged = []
    for rel in extracted_relationships:
        src = resolved.get(rel.get("source_temp_id", ""))
        tgt = resolved.get(rel.get("target_temp_id", ""))
```

**Replace in** `artmind/update.py` (lines 546-564):

```python
                src["name"], rel_type, tgt["name"],
            )
            continue
        edge_id = relation_observation_id(chat_id, src["observation_id"], rel_type, tgt["observation_id"])
        tx.run(
            """
            MATCH (s:Observation {id: $src})
            MATCH (t:Observation {id: $tgt})
            MERGE (s)-[r:ASSERTS_RELATION {id: $id}]->(t)
            SET r.rel_type = $rel_type, r.doc_id = $chat_id, r.chunk_id = $chat_id
            """,
            src=src["observation_id"], tgt=tgt["observation_id"], id=edge_id,
            rel_type=rel_type, chat_id=chat_id,
        )
        written += 1
    return written


def _detect_supersession_candidates(
```

**with:**

```python
                src["name"], rel_type, tgt["name"],
            )
            continue
        staged.append({
            "source_observation_id": src["observation_id"],
            "target_observation_id": tgt["observation_id"],
            "source_name": src["name"],
            "target_name": tgt["name"],
            "rel_type": rel_type,
            "chunk_id": chat_id,
            "doc_id": chat_id,
        })
    return staged


def _detect_supersession_candidates(
```

**Replace in** `artmind/update.py` (lines 670-675):

```python
        resolutions=resolutions,
        extracted_entities=facts["entities"],
        extracted_relationships=facts["relationships"],
    )

    _update_draft_status(draft["id"], "confirmed")
```

**with:**

```python
        resolutions=resolutions,
        extracted_entities=facts["entities"],
        extracted_relationships=facts["relationships"],
        draft_id=draft["id"],
    )

    _update_draft_status(draft["id"], "confirmed")
```

**Replace in** `artmind/vault_sync.py` (lines 1295-1300):

```python

    all_keys: set[tuple[str, str, str]] = set()
    structured_only: list[str] = []  # tables restored to DuckDB, not projected (§14 A2)

    with tempfile.TemporaryDirectory(prefix="artmind-sync-") as scratch_str:
        scratch = Path(scratch_str)
```

**with:**

```python

    all_keys: set[tuple[str, str, str]] = set()
    structured_only: list[str] = []  # tables restored to DuckDB, not projected (§14 A2)
    chat_domains: set[str] = set()   # domains of replayed `update__*` folders (a UserChat each)

    with tempfile.TemporaryDirectory(prefix="artmind-sync-") as scratch_str:
        scratch = Path(scratch_str)
```

**Replace in** `artmind/vault_sync.py` (lines 1356-1361):

```python
                if summary is None:
                    raise VaultSyncError(f"{kg_rel / domain / docdir}: staged KG JSON at {head} could not be read")
                all_keys.update(tuple(k) for k in summary.get("deferred_keys") or [])

    for domain, doc_id in plan.retract:
        result = ingest.retract_document(doc_id, domain)
```

**with:**

```python
                if summary is None:
                    raise VaultSyncError(f"{kg_rel / domain / docdir}: staged KG JSON at {head} could not be read")
                all_keys.update(tuple(k) for k in summary.get("deferred_keys") or [])
                if docdir.startswith(ingest.UPDATE_FOLDER_PREFIX):
                    chat_domains.add(domain)

    for domain, doc_id in plan.retract:
        result = ingest.retract_document(doc_id, domain)
```

**Replace in** `artmind/vault_sync.py` (lines 1390-1395):

```python
        domain_keys = [k for k in all_keys if k[2] == d]
        ingest._sweep_embeddings(d, domain_keys)
        ingest._sweep_chunk_embeddings(domain=d)

    # artmind never commits (spec 2026-09-26, D1): track B's regenerated
    # table__* folders stay in the working tree. (gitignored: spec 2026-09-26 R4)
```

**with:**

```python
        domain_keys = [k for k in all_keys if k[2] == d]
        ingest._sweep_embeddings(d, domain_keys)
        ingest._sweep_chunk_embeddings(domain=d)
    # A replayed `artmind update` is a UserChat, whose vector is never staged.
    for d in sorted(chat_domains):
        from artmind.update import embed_user_chats

        embed_user_chats(domain=d)

    # artmind never commits (spec 2026-09-26, D1): track B's regenerated
    # table__* folders stay in the working tree. (gitignored: spec 2026-09-26 R4)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --group dev pytest test/test_update.py test/test_update_staging.py test/test_vault_sync.py -q`
Expected: `203 passed`. Then the full suite — all pass.

- [ ] **Step 5: Commit**

```bash
git add artmind/ingest.py artmind/update.py artmind/vault_sync.py test/test_update.py test/test_update_staging.py test/test_vault_sync.py
git commit -m "feat(update): confirm writes an update__ staging folder and commits the graph from it; vault sync replays it"
```

---

### Task 8: `artmind update retract` — the graph first, then delete the folder

Decision 3. `update.retract_update(session_id, draft_id=None)` finds the session's `update__<session>__*` folders (or the one draft's), retracts each document id in the graph (`ingest.retract_document`), rebuilds the touched keys with their whole same-as groups, sweeps embeddings, deletes the folders, and marks the drafts (and, for a whole session, the session) `retracted`. The graph goes first, so a failed graph write leaves the folders for a retry. `artmind update retract --session S [--draft N] [--compact]` wraps it; the `update` group docstring and `confirm`'s docstring say a confirmed update is a vault folder.

**Files:**
- Modify: `artmind/cli.py`
- Modify: `artmind/update.py`
- Test: `test/test_update_retract.py` (new)

- [ ] **Step 1: Write the failing tests**

**Create** `test/test_update_retract.py`:

```python
"""`artmind update retract`: a confirmed update leaves the graph, and its
staging folder is deleted so the retraction travels (spec 2026-09-26 §7).
The graph seams are recorded, never a live Neo4j (CLAUDE.md §3)."""
import json

import pytest
from click.testing import CliRunner

import artmind.update as upd


@pytest.fixture()
def kg_dir(tmp_path, monkeypatch):
    import paths

    target = tmp_path / "kg"
    monkeypatch.setattr(paths, "KG_DIR", target)
    return target


def _folder(kg_dir, draft_id, domain="general"):
    from artmind.ingest import write_staging

    folder = kg_dir / domain / f"update__s1__{draft_id}"
    write_staging(folder, {"document.json": {"id": f"update:s1:{draft_id}", "source_kind": "user_chat"},
                           "observations.json": []})
    return folder


@pytest.fixture()
def seams(monkeypatch):
    import artmind.ingest as ing
    import artmind.same_as as same_as
    import artmind.table2graph as t2g

    seen = {"retract": [], "rebuild": [], "drafts": [], "sessions": []}
    monkeypatch.setattr(upd, "_get_update_session", lambda sid: {"session_id": sid, "domain": "general"} if sid == "s1" else None)
    monkeypatch.setattr(ing, "retract_document", lambda doc_id, domain: seen["retract"].append((doc_id, domain)) or {
        "affected_keys": [["alice", "PERSON", "general"]],
    })
    monkeypatch.setattr(same_as, "load_groups", lambda: [[("alice", "PERSON", "general"), ("a. smith", "PERSON", "general")]])
    monkeypatch.setattr(t2g, "_rebuild_in_batches", lambda keys, groups=None: seen["rebuild"].append((sorted(keys), groups)) or {"keys": len(keys)})
    monkeypatch.setattr(ing, "_sweep_embeddings", lambda domain, keys: 0)
    monkeypatch.setattr(upd, "_update_draft_status", lambda draft_id, status: seen["drafts"].append((draft_id, status)))
    monkeypatch.setattr(upd, "_update_session_status", lambda sid, status: seen["sessions"].append((sid, status)))
    return seen


def test_retract_update_retracts_the_graph_then_deletes_the_folder(kg_dir, seams):
    folder = _folder(kg_dir, 7)

    result = upd.retract_update("s1")

    assert seams["retract"] == [("update:s1:7", "general")]
    group = [("alice", "PERSON", "general"), ("a. smith", "PERSON", "general")]
    assert seams["rebuild"] == [(sorted(group), [group])], "the whole same-as group is rebuilt"
    assert not folder.exists()
    assert seams["drafts"] == [(7, "retracted")]
    assert seams["sessions"] == [("s1", "retracted")]
    assert result["retracted"] == ["update:s1:7"]


def test_retract_one_draft_leaves_the_sessions_other_updates(kg_dir, seams):
    _folder(kg_dir, 7)
    keep = _folder(kg_dir, 8)

    upd.retract_update("s1", draft_id=7)

    assert seams["retract"] == [("update:s1:7", "general")]
    assert keep.exists()
    assert seams["sessions"] == []


def test_nothing_to_retract_is_an_error(kg_dir, seams):
    with pytest.raises(ValueError, match="No confirmed update to retract"):
        upd.retract_update("s1")
    with pytest.raises(ValueError, match="No such update session"):
        upd.retract_update("nope")


def test_a_failed_graph_retraction_keeps_the_folder_for_a_retry(kg_dir, seams, monkeypatch):
    import artmind.ingest as ing

    folder = _folder(kg_dir, 7)

    def _boom(doc_id, domain):
        raise RuntimeError("neo4j down")

    monkeypatch.setattr(ing, "retract_document", _boom)

    with pytest.raises(RuntimeError, match="neo4j down"):
        upd.retract_update("s1")
    assert folder.is_dir()


def test_the_cli_retracts_and_prints_json(monkeypatch):
    from artmind.cli import cli

    seen = []
    monkeypatch.setattr(upd, "retract_update", lambda session, draft_id: seen.append((session, draft_id)) or {"retracted": ["update:s1:7"]})

    result = CliRunner().invoke(cli, ["update", "retract", "--session", "s1", "--draft", "7", "--compact"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == {"retracted": ["update:s1:7"]}
    assert seen == [("s1", 7)]


def test_the_cli_reports_a_missing_update(monkeypatch):
    from artmind.cli import cli

    def _missing(session, draft_id):
        raise ValueError("No confirmed update to retract for session 's9'")

    monkeypatch.setattr(upd, "retract_update", _missing)

    result = CliRunner().invoke(cli, ["update", "retract", "--session", "s9"])

    assert result.exit_code != 0
    assert "No confirmed update to retract" in result.output
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --group dev pytest test/test_update_retract.py -q`
Expected: `6 failed` — `AttributeError: module 'artmind.update' has no attribute 'retract_update'` (and the CLI has no `retract` command).

- [ ] **Step 3: Implement**

**Replace in** `artmind/cli.py` (lines 3098-3106):

```python
def update():
    """Add and update knowledge graph facts from natural language.

    Subcommands: draft, confirm, history, export. A fact that retracts an
    existing one is expressed via `confirm`'s `retracts` resolution field, not
    a separate command — see `update confirm --help`.
    """
    pass

```

**with:**

```python
def update():
    """Add and update knowledge graph facts from natural language.

    Subcommands: draft, confirm, retract, history, export. A fact that
    retracts an existing one is expressed via `confirm`'s `retracts`
    resolution field — see `update confirm --help`; `retract` withdraws a
    whole confirmed update. A confirmed update is also a staging folder
    (`.artmind/data/kg/<domain>/update__<session>__<draft>/`), so it — and
    its retraction — reaches every machine through `vault sync`.
    """
    pass

```

**Replace in** `artmind/cli.py` (lines 3127-3133):

```python
@click.option("--session", required=True, help="Session UUID from draft step.")
@click.option("--resolutions", required=True, help="JSON array of resolution objects.")
def update_confirm(session: str, resolutions: str):
    """Write confirmed facts to Neo4j. Returns JSON."""
    _setup_logger()
    env = load_env()
    user_id = env.get("ARTMIND_USER", "unknown")
```

**with:**

```python
@click.option("--session", required=True, help="Session UUID from draft step.")
@click.option("--resolutions", required=True, help="JSON array of resolution objects.")
def update_confirm(session: str, resolutions: str):
    """Write confirmed facts to the vault and Neo4j. Returns JSON.

    Writes a staging folder, .artmind/data/kg/<domain>/update__<session>__<draft>/,
    then commits the graph from it — the same commit `vault sync` replays on
    every other machine. `staging_dir` in the output names the folder.
    """
    _setup_logger()
    env = load_env()
    user_id = env.get("ARTMIND_USER", "unknown")
```

**Replace in** `artmind/cli.py` (lines 3145-3150):

```python



@update.command("history")
@click.option("--domain", default=None, help="Filter by domain.")
@click.option("--user", default=None, help="Filter by created_by.")
```

**with:**

```python



@update.command("retract")
@click.option("--session", required=True, help="Session UUID whose confirmed update(s) to retract.")
@click.option(
    "--draft", "draft_id", type=int, default=None,
    help="Retract only this confirmed draft: the last part of its folder name, "
    "update__<session>__<draft>. Default: every confirmed update in the session.",
)
@click.option("--compact", is_flag=True, help="Emit compact JSON")
def update_retract(session: str, draft_id: int | None, compact: bool):
    """Withdraw a confirmed update: its facts leave the graph and its staging folder is deleted.

    The UserChat and its observations move to history and the entities they
    touched are rebuilt; then the update__<session>__<draft> folder is
    deleted, which Obsidian Git commits — so every other machine's
    `vault sync` retracts it too. Returns JSON.
    """
    _setup_logger()
    try:
        result = update_backend.retract_update(session, draft_id)
    except ValueError as e:
        raise click.ClickException(str(e))
    _echo_json(result, compact)


@update.command("history")
@click.option("--domain", default=None, help="Filter by domain.")
@click.option("--user", default=None, help="Filter by created_by.")
```

**Replace in** `artmind/update.py` (lines 727-732):

```python
    return result


def export_chats(
    domain: str | None, format: str, output_dir: Path
) -> list[Path]:
```

**with:**

```python
    return result


def retract_update(session_id: str, draft_id: int | None = None) -> dict:
    """Retract a confirmed update -- every confirmed draft of `session_id`,
    or only `draft_id`.

    Its UserChat and observations move to history (`ingest.retract_document`,
    the retraction `vault sync` applies when a staging folder disappears),
    the keys it touched are rebuilt with every same-as group touching them,
    and then its `update__<session_id>__<draft_id>` staging folder is
    deleted. The deletion is what travels: Obsidian Git commits it, and
    every other machine's `vault sync` retracts the same UserChat. The graph
    goes first, so a failed graph write leaves the folder for a retry.
    """
    import shutil

    import paths
    from artmind import ingest, projection, same_as
    from artmind.atomic_dir import is_scratch
    from artmind.table2graph import _rebuild_in_batches

    session = _get_update_session(session_id)
    if not session:
        raise ValueError(f"No such update session: {session_id!r}")
    domain = session["domain"]
    suffix = "*" if draft_id is None else str(draft_id)
    folders = sorted(
        p for p in (Path(paths.KG_DIR) / domain).glob(f"{UPDATE_FOLDER_PREFIX}{session_id}__{suffix}")
        if p.is_dir() and not is_scratch(p)
    )
    if not folders:
        which = f"draft {draft_id} of session" if draft_id is not None else "session"
        raise ValueError(f"No confirmed update to retract for {which} {session_id!r} (no {UPDATE_FOLDER_PREFIX}* folder)")

    retracted: list[str] = []
    keys: set = set()
    for folder in folders:
        doc_id = json.loads((folder / "document.json").read_text(encoding="utf-8"))["id"]
        result = ingest.retract_document(doc_id, domain)
        keys |= {tuple(k) for k in result.get("affected_keys") or []}
        retracted.append(doc_id)
    groups = same_as.load_groups()
    keys = projection.affected_keys(incoming=sorted(keys), same_as_groups=groups)
    summary = _rebuild_in_batches(sorted(keys), groups=groups) if keys else {"keys": 0}
    for d in sorted({k[2] for k in keys}):
        ingest._sweep_embeddings(d, [k for k in keys if k[2] == d])

    for folder in folders:
        shutil.rmtree(folder)
        draft = folder.name.rsplit("__", 1)[-1]
        if draft.isdigit():
            _update_draft_status(int(draft), "retracted")
    if draft_id is None:
        _update_session_status(session_id, "retracted")
    logger.info("update: retracted {} ({} folder(s) removed)", ", ".join(retracted), len(folders))
    return {
        "session_id": session_id,
        "retracted": retracted,
        "folders_removed": [str(f) for f in folders],
        "projection": summary,
    }


def export_chats(
    domain: str | None, format: str, output_dir: Path
) -> list[Path]:
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --group dev pytest test/test_update_retract.py -q`
Expected: `6 passed`. Then the full suite — all pass (`test_cli_guide.py` included: `update` has no `COMMAND_GROUPS` entry of its own, so its new subcommand is routed with it).

- [ ] **Step 5: Commit**

```bash
git add artmind/cli.py artmind/update.py test/test_update_retract.py
git commit -m "feat(update): update retract -- the graph first, then delete the staging folder so the retraction travels"
```

---

### Task 9: pending work and the staleness line count curation records and same-as groups (spec §6 A4)

Decision 9. `_store_pending` reports two more counts, `curation` and `same_as_groups` (0 for the structured store), and a store is `behind` when any count is non-zero. `_graph_pending` counts a curation record to apply unless the graph holds its committed fingerprint, a removal while the graph still holds it (one fingerprint read per kind in the range), and every changed same-as group. `curation_phrase` renders ` / K curation records / G same-as groups` (each part only when non-zero) for the staleness line and for `vault status`'s graph line. Update folders need nothing new: they are documents, and Task 6 made the fingerprint read cover `:UserChat`.

Two existing tests compare whole pending reports and gain the two keys.

**Files:**
- Modify: `artmind/cli.py`
- Modify: `artmind/vault_sync.py`
- Test: `test/test_vault_sync.py`

- [ ] **Step 1: Write the failing tests**

**Replace in** `test/test_vault_sync.py` (lines 2767-2775):

```python

    pending = vs.pending_work(repo, c3, marks)

    assert pending["graph"] == {"bookmark": c3, "state": "current", "docs": 0, "tables": [], "detail": None}
    assert pending["structured"] == {
        "bookmark": c3, "state": "current", "docs": 0, "tables": [], "detail": None,
    }


```

**with:**

```python

    pending = vs.pending_work(repo, c3, marks)

    assert pending["graph"] == {
        "bookmark": c3, "state": "current", "docs": 0, "tables": [], "curation": 0, "same_as_groups": 0, "detail": None,
    }
    assert pending["structured"] == {
        "bookmark": c3, "state": "current", "docs": 0, "tables": [], "curation": 0, "same_as_groups": 0, "detail": None,
    }


```

**Replace in** `test/test_vault_sync.py` (lines 2783-2791):

```python

    pending = vs.pending_work(repo, c3, marks)

    assert pending["graph"] == {"bookmark": None, "state": "no_bookmark", "docs": 0, "tables": [], "detail": None}
    assert pending["structured"] == {
        "bookmark": None, "state": "no_bookmark", "docs": 0, "tables": [], "detail": None,
    }


```

**with:**

```python

    pending = vs.pending_work(repo, c3, marks)

    assert pending["graph"] == {
        "bookmark": None, "state": "no_bookmark", "docs": 0, "tables": [], "curation": 0, "same_as_groups": 0, "detail": None,
    }
    assert pending["structured"] == {
        "bookmark": None, "state": "no_bookmark", "docs": 0, "tables": [], "curation": 0, "same_as_groups": 0, "detail": None,
    }


```

**Replace in** `test/test_vault_sync.py` (lines 3512-3514):

```python
    vs.sync(repo)

    assert calls["retract_document"] == [(chat_id, "general")]
```

**with:**

```python
    vs.sync(repo)

    assert calls["retract_document"] == [(chat_id, "general")]


# ── pending work counts updates, curation records and same-as groups (§6 A4) ──


def test_pending_work_counts_an_update_folder_the_graph_lacks_as_a_doc(repo, monkeypatch, graph):
    from artmind import sync_state

    kg_dir = _patch_kg_dir(monkeypatch, repo)
    _patch_structured_text_dir(monkeypatch, repo)
    (repo / "a.txt").write_text("x")
    _commit_all(repo, "base")
    base = vs.head_sha(repo)
    folder, chat_id = _write_update_folder(kg_dir, "general", "s1", 7)
    _commit_all(repo, "an update")
    head = vs.head_sha(repo)

    pending = vs.pending_work(repo, head, vs.Bookmarks(graph=base, structured=head))
    assert (pending["graph"]["state"], pending["graph"]["docs"]) == ("behind", 1)

    graph.fingerprints[chat_id] = sync_state.folder_fingerprint(folder)
    pending = vs.pending_work(repo, head, vs.Bookmarks(graph=base, structured=head))
    assert (pending["graph"]["state"], pending["graph"]["docs"]) == ("current", 0)


def test_pending_work_counts_only_curation_records_the_graph_does_not_hold(repo, monkeypatch, curation_graph):
    from artmind import curation_records

    base = _curation_base(repo, monkeypatch, _conflict("gone"), _conflict("kept-gone"))
    fp = curation_records.write_record("conflicts", _conflict("applied"))
    curation_records.write_record("conflicts", _conflict("new"))
    curation_records.delete_record("conflicts", "gone")
    curation_records.delete_record("conflicts", "kept-gone")
    _commit_all(repo, "two added, two removed")
    head = vs.head_sha(repo)
    # "applied" is already in the graph at this version; "gone" is still in
    # the graph; "kept-gone" was already removed from it.
    curation_graph.fingerprints = {"applied": fp, "gone": "whatever"}

    pending = vs.pending_work(repo, head, vs.Bookmarks(graph=base, structured=head))

    assert (pending["graph"]["state"], pending["graph"]["curation"], pending["graph"]["docs"]) == ("behind", 2, 0)


def test_pending_work_counts_changed_same_as_groups(repo, monkeypatch, graph):
    path, base = _same_as_base(repo, monkeypatch, _ACME)
    _write_groups(path, _ACME, _FCA)
    _commit_all(repo, "add fca")
    head = vs.head_sha(repo)

    pending = vs.pending_work(repo, head, vs.Bookmarks(graph=base, structured=head))

    assert (pending["graph"]["state"], pending["graph"]["same_as_groups"]) == ("behind", 1)


def test_the_staleness_line_names_curation_and_same_as_only_when_pending():
    report = {"bookmark": "b", "state": "behind", "docs": 1, "tables": [], "curation": 2, "same_as_groups": 1, "detail": None}

    assert vs.staleness_message({"graph": report, "structured": _current("b")}) == (
        "artmind: graph is 1 docs / 0 tables / 2 curation records / 1 same-as groups behind the vault"
        " — run `artmind vault sync`"
    )
    only_curation = dict(report, docs=0, same_as_groups=0)
    assert vs.staleness_message({"graph": only_curation, "structured": _current("b")}) == (
        "artmind: graph is 0 docs / 0 tables / 2 curation records behind the vault — run `artmind vault sync`"
    )
    assert vs.staleness_message({"graph": _current("b"), "structured": _current("b")}) is None


def test_vault_status_prints_pending_curation_on_the_graph_line(capsys):
    from artmind.cli import _echo_sync_status

    _echo_sync_status({
        "head": "h", "graph_error": None, "legacy_cursor": None, "operation_in_progress": None,
        "unresolved_conflicts": [], "message": None,
        "stores": {
            "graph": {"bookmark": "b", "state": "behind", "docs": 0, "tables": [], "curation": 3,
                      "same_as_groups": 0, "detail": None},
            "structured": {"bookmark": "b", "state": "current", "docs": 0, "tables": [], "detail": None},
        },
    })

    assert "graph      b  0 docs / 0 tables / 3 curation records behind" in capsys.readouterr().out
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --group dev pytest test/test_vault_sync.py -q`
Expected: `6 failed, 158 passed` — the two retargeted report comparisons (`AssertionError`: no `curation`/`same_as_groups` keys), the curation and same-as pending tests (`KeyError: 'curation'` / `'same_as_groups'`), the staleness-line test and the `vault status` line test (`AssertionError`: the line has no curation part). `test_pending_work_counts_an_update_folder_the_graph_lacks_as_a_doc` already passes — it pins that update folders are counted as documents.

- [ ] **Step 3: Implement**

**Replace in** `artmind/cli.py` (lines 3508-3514):

```python

def _echo_sync_status(sync: dict) -> None:
    """The `vault sync` half of `vault status`, human-readable."""
    from artmind.vault_sync import _one_line

    click.echo(f"HEAD:     {sync['head'] or '(no commits yet)'}")
    for store, label in (("graph", "graph     "), ("structured", "structured")):
```

**with:**

```python

def _echo_sync_status(sync: dict) -> None:
    """The `vault sync` half of `vault status`, human-readable."""
    from artmind.vault_sync import _one_line, curation_phrase

    click.echo(f"HEAD:     {sync['head'] or '(no commits yet)'}")
    for store, label in (("graph", "graph     "), ("structured", "structured")):
```

**Replace in** `artmind/cli.py` (lines 3520-3526):

```python
        state = report.get("state")
        if state == "behind":
            tables = len(report.get("tables") or [])
            what = f"{report['docs']} docs / {tables} tables behind" if store == "graph" else f"{tables} tables behind"
        else:
            what = {
                "current": "current",
```

**with:**

```python
        state = report.get("state")
        if state == "behind":
            tables = len(report.get("tables") or [])
            what = (
                f"{report['docs']} docs / {tables} tables{curation_phrase(report)} behind"
                if store == "graph" else f"{tables} tables behind"
            )
        else:
            what = {
                "current": "current",
```

**Replace in** `artmind/vault_sync.py` (lines 1461-1474):

```python
def _store_pending(
    vault_dir: Path, store: str, bookmark: str | None, head: str, *, timeout: float | None
) -> dict:
    """`{"bookmark", "state", "docs", "tables", "detail"}` for one store.
    `state` is `current`, `behind`, `no_bookmark`, `not_ancestor` (the
    bookmark is not in HEAD's history: pull first) or `error` (the range
    cannot be classified -- e.g. a table mapping broken at HEAD; `detail`
    says why, and `vault sync` would refuse with the same message). `docs`
    counts documents still to replay or retract after the fingerprint check
    (graph only); `tables` lists `(domain, table)` still to restore/project."""
    report = {"bookmark": bookmark, "state": "current", "docs": 0, "tables": [], "detail": None}
    if bookmark is None:
        report["state"] = "no_bookmark"
        return report
```

**with:**

```python
def _store_pending(
    vault_dir: Path, store: str, bookmark: str | None, head: str, *, timeout: float | None
) -> dict:
    """`{"bookmark", "state", "docs", "tables", "curation", "same_as_groups",
    "detail"}` for one store. `state` is `current`, `behind`, `no_bookmark`,
    `not_ancestor` (the bookmark is not in HEAD's history: pull first) or
    `error` (the range cannot be classified -- e.g. a table mapping broken at
    HEAD; `detail` says why, and `vault sync` would refuse with the same
    message). `docs` counts documents -- `artmind update` folders included --
    still to replay or retract after the fingerprint check (graph only);
    `tables` lists `(domain, table)` still to restore/project; `curation`
    counts curation records still to apply or remove after their own
    fingerprint check, and `same_as_groups` the same-as groups changed in the
    range (graph only)."""
    report = {
        "bookmark": bookmark, "state": "current", "docs": 0, "tables": [],
        "curation": 0, "same_as_groups": 0, "detail": None,
    }
    if bookmark is None:
        report["state"] = "no_bookmark"
        return report
```

**Replace in** `artmind/vault_sync.py` (lines 1484-1492):

```python
    try:
        if store == "structured":
            tables, _ = _classify_structured_text_diff(vault_dir, bookmark, head, None)
            docs = 0
        else:
            docs, tables = _graph_pending(vault_dir, bookmark, head, timeout=timeout)
    except VaultSyncError as e:
        report.update(state="error", detail=str(e))
        return report
```

**with:**

```python
    try:
        if store == "structured":
            tables, _ = _classify_structured_text_diff(vault_dir, bookmark, head, None)
            docs = curation = same_as_groups = 0
        else:
            docs, tables, curation, same_as_groups = _graph_pending(vault_dir, bookmark, head, timeout=timeout)
    except VaultSyncError as e:
        report.update(state="error", detail=str(e))
        return report
```

**Replace in** `artmind/vault_sync.py` (lines 1500-1516):

```python
        # path can legitimately raise, so a genuine bug still crashes.
        report.update(state="error", detail=f"graph unreachable: {e}")
        return report
    report.update(state="behind" if docs or tables else "current", docs=docs, tables=sorted(tables))
    return report


def _graph_pending(
    vault_dir: Path, bookmark: str, head: str, *, timeout: float | None
) -> tuple[int, set[tuple[str, str]]]:
    """`(docs, tables)` the graph still has to apply between `bookmark` and
    `head`: a replay counts unless the graph already carries its committed
    fingerprint; a retraction counts only while the graph still has the
    document. ONE Cypher read covers both."""
    from artmind import sync_state

    plan = classify_diff(vault_dir, bookmark, head)
```

**with:**

```python
        # path can legitimately raise, so a genuine bug still crashes.
        report.update(state="error", detail=f"graph unreachable: {e}")
        return report
    behind = docs or tables or curation or same_as_groups
    report.update(
        state="behind" if behind else "current", docs=docs, tables=sorted(tables),
        curation=curation, same_as_groups=same_as_groups,
    )
    return report


def _graph_pending(
    vault_dir: Path, bookmark: str, head: str, *, timeout: float | None
) -> tuple[int, set[tuple[str, str]], int, int]:
    """`(docs, tables, curation, same_as_groups)` the graph still has to
    apply between `bookmark` and `head`: a replay counts unless the graph
    already carries its committed fingerprint; a retraction counts only while
    the graph still has the document. ONE Cypher read covers both. Curation
    records are counted the same way (one read per kind in the range), and
    every same-as group changed in the range counts -- the graph keeps no
    per-group fingerprint to compare against."""
    from artmind import sync_state

    plan = classify_diff(vault_dir, bookmark, head)
```

**Replace in** `artmind/vault_sync.py` (lines 1523-1529):

```python
    tables = set(plan.regenerate_tables)
    tables |= {tuple(doc_id.split(":", 2)[1:]) for doc_id in retract if doc_id.startswith("table:")}
    docs = len(replay) + sum(1 for doc_id in retract if not doc_id.startswith("table:"))
    return docs, tables


def pending_work(
```

**with:**

```python
    tables = set(plan.regenerate_tables)
    tables |= {tuple(doc_id.split(":", 2)[1:]) for doc_id in retract if doc_id.startswith("table:")}
    docs = len(replay) + sum(1 for doc_id in retract if not doc_id.startswith("table:"))
    curation = 0
    if plan.curation:
        committed_records = committed_curation_fingerprints(vault_dir, head, plan)
        held = _graph_curation_fingerprints(plan.curation, timeout=timeout)
        curation = sum(
            1 for kind, record_id, action in plan.curation
            if (action == "apply" and held.get((kind, record_id)) != committed_records.get((kind, record_id)))
            or (action == "remove" and (kind, record_id) in held)
        )
    return docs, tables, curation, plan.same_as_groups


def pending_work(
```

**Replace in** `artmind/vault_sync.py` (lines 1548-1557):

```python
    return " ".join(str(detail).split())


def staleness_message(pending: dict) -> str | None:
    """The one advisory line for stderr, or None when nothing is pending.
    `M tables` counts every table either store still has to apply -- a table
    restored into DuckDB is also what the graph projects."""
    graph = pending.get("graph", {})
    structured = pending.get("structured", {})
    for name, report in (("graph", graph), ("structured", structured)):
```

**with:**

```python
    return " ".join(str(detail).split())


def curation_phrase(report: dict) -> str:
    """` / K curation records / G same-as groups` for a graph store's
    pending report -- each part only when non-zero, so the line reads as
    before when no curation is pending."""
    parts = []
    if report.get("curation"):
        parts.append(f"{report['curation']} curation records")
    if report.get("same_as_groups"):
        parts.append(f"{report['same_as_groups']} same-as groups")
    return "".join(f" / {part}" for part in parts)


def staleness_message(pending: dict) -> str | None:
    """The one advisory line for stderr, or None when nothing is pending.
    `M tables` counts every table either store still has to apply -- a table
    restored into DuckDB is also what the graph projects. Pending curation
    records and same-as groups follow, when there are any."""
    graph = pending.get("graph", {})
    structured = pending.get("structured", {})
    for name, report in (("graph", graph), ("structured", structured)):
```

**Replace in** `artmind/vault_sync.py` (lines 1576-1586):

```python
        )
    tables = {tuple(t) for t in graph.get("tables", [])} | {tuple(t) for t in structured.get("tables", [])}
    docs = graph.get("docs", 0)
    if not docs and not tables:
        return None
    if not docs and not graph.get("tables"):
        return f"artmind: structured store is {len(tables)} tables behind the vault — run `artmind vault sync`"
    return f"artmind: graph is {docs} docs / {len(tables)} tables behind the vault — run `artmind vault sync`"


def query_staleness_warning(vault_dir: Path | None) -> str | None:
```

**with:**

```python
        )
    tables = {tuple(t) for t in graph.get("tables", [])} | {tuple(t) for t in structured.get("tables", [])}
    docs = graph.get("docs", 0)
    extra = curation_phrase(graph)
    if not docs and not tables and not extra:
        return None
    if not docs and not graph.get("tables") and not extra:
        return f"artmind: structured store is {len(tables)} tables behind the vault — run `artmind vault sync`"
    return f"artmind: graph is {docs} docs / {len(tables)} tables{extra} behind the vault — run `artmind vault sync`"


def query_staleness_warning(vault_dir: Path | None) -> str | None:
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --group dev pytest test/test_vault_sync.py -q`
Expected: `164 passed`. Then the full suite — all pass.

- [ ] **Step 5: Commit**

```bash
git add artmind/cli.py artmind/vault_sync.py test/test_vault_sync.py
git commit -m "feat(vault-sync): pending work and the staleness line count curation records and same-as groups"
```

---

### Task 10: docs, docstrings, skills, spec amendment; full suite and CLI help

Prose only: `docs/vault.md` (layout, what `vault sync` replays, a new "Curation that travels" section, `vault status`), the `vault sync` CLI docstring, `vault_sync`'s module docstring, the `artmind-update` skill (`staging_dir`; retracting a confirmed update), the `artmind-curate` skill (what travels; `same_as.yaml` in a vault; a resolution is recorded), and the spec (§7's table, §11's track test line, §14 A6's `conflicts.yaml` wording, and a new §15 A9 recording decisions 1–8). The update skill's grounding test (`test_update_skill_grounding.py`) still passes: the sections it slices are untouched.

**Files:**
- Modify: `artmind/cli.py`
- Modify: `artmind/skills/artmind-curate/SKILL.md`
- Modify: `artmind/skills/artmind-update/SKILL.md`
- Modify: `artmind/vault_sync.py`
- Modify: `docs/superpowers/specs/2026-09-26-vault-git-transport-design.md`
- Modify: `docs/vault.md`

- [ ] **Step 1: Apply the prose changes**

**Replace in** `artmind/cli.py` (lines 3601-3607):

```python
@click.option("--dryRun", "dry_run", is_flag=True, help="Report the classified diff (documents to replay/retract, tables to regenerate) without writing anything.")
@click.option("--compact", is_flag=True, help="Emit compact JSON")
def vault_sync_cmd(bootstrap_empty, bootstrap_synced, store, domain, dry_run, compact):
    """Replay committed KG-staging, structured-text, table-mapping and schema changes into Neo4j/DuckDB since each store's bookmark.

    Detects exactly which document folders under .artmind/data/kg/**, which
    structured-store tables under .artmind/data/structured_text/**, and which
```

**with:**

```python
@click.option("--dryRun", "dry_run", is_flag=True, help="Report the classified diff (documents to replay/retract, tables to regenerate) without writing anything.")
@click.option("--compact", is_flag=True, help="Emit compact JSON")
def vault_sync_cmd(bootstrap_empty, bootstrap_synced, store, domain, dry_run, compact):
    """Replay committed KG-staging, structured-text, table-mapping, schema and curation changes into Neo4j/DuckDB since each store's bookmark.

    Detects exactly which document folders under .artmind/data/kg/**, which
    structured-store tables under .artmind/data/structured_text/**, and which
```

**Replace in** `artmind/cli.py` (lines 3614-3619):

```python
    mapping names is restored to the structured store only. Complements (does
    not replace) `session close`/`session initiate`'s whole-graph snapshot.

    Each store keeps its own bookmark: the graph's lives in the graph
    (one :ArtmindSyncState node per vault_id, so a shared AuraDB carries one
    bookmark for every machine), the structured store's in this machine's
```

**with:**

```python
    mapping names is restored to the structured store only. Complements (does
    not replace) `session close`/`session initiate`'s whole-graph snapshot.

    Curation travels the same way: an `artmind update` is a document folder
    (kg/<domain>/update__<session>__<draft>/, replayed as a UserChat); a
    curation record under .artmind/data/curation/<kind>/ (a detected or
    resolved conflict) is applied when added or changed and removed when
    deleted; and the members of every same-as group added, removed or
    changed in .artmind/same_as.yaml are rebuilt, with the groups as
    committed at HEAD. Same-as proposals stay machine-local.

    Each store keeps its own bookmark: the graph's lives in the graph
    (one :ArtmindSyncState node per vault_id, so a shared AuraDB carries one
    bookmark for every machine), the structured store's in this machine's
```

**Replace in** `artmind/skills/artmind-curate/SKILL.md` (lines 19-24):

```markdown
Structured-table classifications (Workflow D) are lighter still: a registry
row, cleared or overwritten with no graph write involved.

## Required Inputs

- `domain` (one or more): ask if not provided. Pass every sibling domain the
```

**with:**

```markdown
Structured-table classifications (Workflow D) are lighter still: a registry
row, cleared or overwritten with no graph write involved.

**What travels to other machines.** In a vault, `same_as.yaml` is
`.artmind/same_as.yaml` (committed), and every detected conflict — with its
resolution — is its own file under `.artmind/data/curation/conflicts/`.
Obsidian Git commits both, and `artmind vault sync` on another machine
rebuilds the changed same-as groups and applies the conflict records, so a
machine with its own Neo4j ends up with the same curation. Same-as
**proposals** (the review queue) do not travel: they stay on the machine
that ran `sameas propose` / `detect-conflicts` / `refine-graph`, and only an
approved group does.

## Required Inputs

- `domain` (one or more): ask if not provided. Pass every sibling domain the
```

**Replace in** `artmind/skills/artmind-curate/SKILL.md` (lines 128-135):

```markdown
Same-as groups are the reversible mechanism they're described as above — this
is not a forensic recovery, it's the ordinary undo path:

1. Open `same_as.yaml` (in the run folder) and remove the offending group, or
   remove just the wrong member from it.
2. `artmind projection rebuild --domain <d> --compact` (or a bare
   `artmind projection rebuild --compact` to also clear drift).
3. The un-merged entity returns under its original deterministic id —
```

**with:**

```markdown
Same-as groups are the reversible mechanism they're described as above — this
is not a forensic recovery, it's the ordinary undo path:

1. Open `same_as.yaml` (in the run folder — `.artmind/same_as.yaml` in a
   vault, where other machines pick the edit up with `vault sync`) and remove
   the offending group, or remove just the wrong member from it.
2. `artmind projection rebuild --domain <d> --compact` (or a bare
   `artmind projection rebuild --compact` to also clear drift).
3. The un-merged entity returns under its original deterministic id —
```

**Replace in** `artmind/skills/artmind-curate/SKILL.md` (lines 343-349):

````markdown
```

Use `--status dismissed` for a false positive. `query graph conflicts --status all`
shows closed ones afterwards. This applies to adjudicator-produced conflicts
(`_source: 'adjudicator'`, the ones `query graph conflicts` surfaces);
projection-produced conflicts (`_source: 'projection'`, a single entity's own
property disputed within one instant — see artmind-query's Adjudicate step)
````

**with:**

````markdown
```

Use `--status dismissed` for a false positive. `query graph conflicts --status all`
shows closed ones afterwards. The resolution is written to the conflict's
record file (`.artmind/data/curation/conflicts/<id>.json`), so it travels to
every machine with `vault sync`; re-detecting the conflict later never
reopens it. This applies to adjudicator-produced conflicts
(`_source: 'adjudicator'`, the ones `query graph conflicts` surfaces);
projection-produced conflicts (`_source: 'projection'`, a single entity's own
property disputed within one instant — see artmind-query's Adjudicate step)
````

**Replace in** `artmind/skills/artmind-update/SKILL.md` (lines 148-154):

````markdown
  --resolutions '<resolutions JSON>'
```

Output JSON: `{nodes_created, nodes_updated, nodes_retracted, relationships_written, user_chat_id}`

Report the summary to the user:
> "Added: 2 new nodes, 1 updated, 1 relationship written, 1 fact retracted."
````

**with:**

````markdown
  --resolutions '<resolutions JSON>'
```

Output JSON: `{nodes_created, nodes_updated, nodes_retracted, relationships_written, user_chat_id, staging_dir}`

A confirm is also a vault file: `staging_dir` is the folder it wrote,
`.artmind/data/kg/<domain>/update__<session_id>__<draft_id>/`, and the graph
was committed *from* that folder. Obsidian Git commits it like any other
change, and every other machine's `artmind vault sync` replays it — so a
fact added here reaches a machine with its own Neo4j too. Don't edit the
folder by hand.

Report the summary to the user:
> "Added: 2 new nodes, 1 updated, 1 relationship written, 1 fact retracted."
````

**Replace in** `artmind/skills/artmind-update/SKILL.md` (lines 159-164):

````markdown
artmind query graph pattern2 --domain <domain> --entityNameList "<new entity name>" --compact
```

## Step 4 — Continue or Exit

Ask: "Anything else to add to this session?"
````

**with:**

````markdown
artmind query graph pattern2 --domain <domain> --entityNameList "<new entity name>" --compact
```

## Retracting a confirmed update

If the user says a fact they confirmed earlier was wrong — the whole input,
not one relationship (that is Step 2b's `retracts`) — withdraw it:

```bash
artmind update retract --session <session_id> [--draft <draft_id>] --compact
```

`--draft` is the last part of the update's folder name
(`update__<session_id>__<draft_id>`); without it, every confirmed update in
the session is retracted. Its UserChat and observations move to history, the
entities they touched are rebuilt, and the folder is deleted — the deletion
travels, so `vault sync` retracts it on every other machine. Confirm with the
user before running it: it is the only way this skill removes facts.

## Step 4 — Continue or Exit

Ask: "Anything else to add to this session?"
````

**Replace in** `artmind/vault_sync.py` (lines 18-23):

```python
whose committed fingerprint the graph already carries is skipped (§6 A3),
and `pending_work`/`query_staleness_warning` report what is still to apply
(§6 A4). artmind never writes to this vault's git (D2) -- only reads it.
"""
from __future__ import annotations

```

**with:**

```python
whose committed fingerprint the graph already carries is skipped (§6 A3),
and `pending_work`/`query_staleness_warning` report what is still to apply
(§6 A4). artmind never writes to this vault's git (D2) -- only reads it.

Curation travels too (spec §7, §15 A9): `artmind update` folders
(`kg/<domain>/update__*`) replay with track A; track C applies curation
records (`.artmind/data/curation/<kind>/<id>.json`, `artmind.curation_records`)
and track D rebuilds the members of every same-as group changed in
`.artmind/same_as.yaml`, with the groups as committed at `head`.
"""
from __future__ import annotations

```

**Replace in** `docs/superpowers/specs/2026-09-26-vault-git-transport-design.md` (lines 327-334):

```markdown

| Write today | Stored as | New file in the vault | Applied by |
|---|---|---|---|
| `artmind update` facts (`update.py`, direct Cypher) | Neo4j + gitignored registry drafts | `.artmind/data/kg/<domain>/update__<session_id>/` — `document.json` + `observations.json`, the same shape as a document | track A replay, like any document |
| Conflict resolutions (`conflicts.resolve_conflict`) | `:Conflict.status` only | `.artmind/data/curation/conflicts.yaml` keyed by conflict id (status, reason, resolved_at) | new track C: set statuses from the file |
| `same_as.yaml` edits | already a vault file | — | new track D: rebuild the keys in groups added/removed/changed between base and head |

`update confirm` keeps writing Neo4j immediately (the user expects the fact now) **and**
```

**with:**

```markdown

| Write today | Stored as | New file in the vault | Applied by |
|---|---|---|---|
| `artmind update` facts (`update.py`, direct Cypher) | Neo4j + gitignored registry drafts | `.artmind/data/kg/<domain>/update__<session_id>__<draft_id>/` — `document.json` + `observations.json`, the same shape as a document (§14 A9) | track A replay, like any document |
| Conflicts and their resolutions (`conflicts.materialize`, `conflicts.resolve_conflict`) | `:Conflict` nodes only | `.artmind/data/curation/conflicts/<id>.json`, one file per conflict record (§14 A6, A9) | new track C: MERGE the record, or remove it when its file is deleted |
| `same_as.yaml` edits | already a vault file | — | new track D: rebuild the keys in groups added/removed/changed between base and head |

`update confirm` keeps writing Neo4j immediately (the user expects the fact now) **and**
```

**Replace in** `docs/superpowers/specs/2026-09-26-vault-git-transport-design.md` (lines 400-407):

```markdown
  A bookmark whose commit has been rewritten away (no longer an ancestor of `HEAD`) is
  refused, not silently diffed.
- **Preflight:** `MERGE_HEAD` present → refuses, bookmarks untouched.
- **§7 tracks:** an `update__*` folder replays; a `conflicts.yaml` change sets
  `:Conflict.status`; a `same_as.yaml` group change rebuilds exactly its keys.
- **Legacy compatibility:** a vault with `manifest.json` and fat frontmatter still
  imports and versions correctly.

```

**with:**

```markdown
  A bookmark whose commit has been rewritten away (no longer an ancestor of `HEAD`) is
  refused, not silently diffed.
- **Preflight:** `MERGE_HEAD` present → refuses, bookmarks untouched.
- **§7 tracks:** an `update__*` folder replays; a changed conflict record file
  MERGEs its `:Conflict`; a `same_as.yaml` group change rebuilds exactly its keys.
- **Legacy compatibility:** a vault with `manifest.json` and fat frontmatter still
  imports and versions correctly.

```

**Replace in** `docs/superpowers/specs/2026-09-26-vault-git-transport-design.md` (lines 477-485):

```markdown

**A6. Conflict records travel, not just their status (amends §7).** Conflicts are *detected* by
an LLM and written only to Neo4j, so a machine with its own Neo4j never sees them at all.
`.artmind/data/curation/conflicts.yaml` holds the conflict record itself (id, the two entity
keys, aspect, verdict, evidence summary, status, reason, timestamps); apply MERGEs `:Conflict`
nodes and `CONFLICTS_WITH` edges from it. Ids are deterministic (§13), so re-detecting the same
conflict on another machine MERGEs onto the same record rather than duplicating it.
Same-as **proposals** stay machine-local: they are a review queue, and an approved group already
travels as `same_as.yaml`.
```

**with:**

```markdown

**A6. Conflict records travel, not just their status (amends §7).** Conflicts are *detected* by
an LLM and written only to Neo4j, so a machine with its own Neo4j never sees them at all.
The conflict record itself (id, the two entity keys, aspect, verdict, evidence summary, status,
reason, timestamps) travels as a vault file -- one file per conflict, not a shared
`conflicts.yaml` (§14 A9); apply MERGEs `:Conflict` nodes and `CONFLICTS_WITH` edges from it. Ids are deterministic (§13), so re-detecting the same
conflict on another machine MERGEs onto the same record rather than duplicating it.
Same-as **proposals** stay machine-local: they are a review queue, and an approved group already
travels as `same_as.yaml`.
```

**Replace in** `docs/superpowers/specs/2026-09-26-vault-git-transport-design.md` (lines 489-491):

```markdown

**A8. Automatic apply is in scope (amends §12 item 6).** Opt-in `ARTMIND_VAULT_AUTO_APPLY=1`:
`serve` polls `HEAD` and runs the same apply when the preflight passes and the graph is behind.
```

**with:**

```markdown

**A8. Automatic apply is in scope (amends §12 item 6).** Opt-in `ARTMIND_VAULT_AUTO_APPLY=1`:
`serve` polls `HEAD` and runs the same apply when the preflight passes and the graph is behind.

## 15. Amendments (2026-09-28, phase 4 -- curation travels)

**A9. One file per curation record; update folders per confirmed draft (amends §7, §14 A6).**
Every path under `.artmind/data/**` is `merge=binary` (R3), so one shared `conflicts.yaml` would
turn two machines' independent curation into a whole-file conflict that blocks `vault sync`.
Each curation record is its own file, `.artmind/data/curation/<kind>/<id>.json`, written
atomically and serialised deterministically; `conflicts` is the first kind
(`artmind/curation_records.py`, `artmind/conflict_records.py`). A detection never rewrites an
existing record, so a conflict another machine recorded -- and perhaps resolved -- is neither
reopened nor churned; two machines colliding on one record only happens when both detect it
before either pulls, and then either side may be kept. The graph carries each record's
fingerprint (`:Conflict.record_fingerprint`), so a shared graph skips records it already has, and
`vault status` counts only what is really pending.

An `artmind update` is one folder per confirmed draft,
`kg/<domain>/update__<session_id>__<draft_id>/`, not per session: a session can confirm several
drafts. Its id, `update:<session_id>:<draft_id>`, is deterministic (a retried confirm rewrites the
same folder) and cannot collide across machines (a session's uuid4 lives in one machine's
registry). `update confirm` writes the folder and commits the graph from it through
`ingest._commit_document_tx` -- the transaction `vault sync` replays it with -- as a `:UserChat`
(not a `:Document`). Deleting the folder (`artmind update retract`, or by hand) retracts it
everywhere.

Track D rebuilds with `same_as.yaml` as committed at `head`, never the working tree, and expands
every key it rebuilds to the whole same-as groups touching it.

```

**Replace in** `docs/vault.md` (lines 90-95):

```markdown
        │   ├── a_deck_artifacts/       ← extracted images + their descriptions
        │   └── a_deck_chunks/          ← chunk_001.md, chunks_meta.json
        ├── kg/<domain>/<doc>/          ← extraction output; COMMITTED
        ├── document_registry.db        ← path↔id cache; NOT committed
        ├── graph_snapshot/             ← *.tar.gz; NOT committed
        └── structured_snapshot/        ← *.tar.gz; NOT committed
```

**with:**

```markdown
        │   ├── a_deck_artifacts/       ← extracted images + their descriptions
        │   └── a_deck_chunks/          ← chunk_001.md, chunks_meta.json
        ├── kg/<domain>/<doc>/          ← extraction output; COMMITTED
        ├── kg/<domain>/update__<session>__<draft>/  ← an `artmind update` fact; COMMITTED
        ├── curation/<kind>/<id>.json   ← curation records (conflicts); COMMITTED
        ├── document_registry.db        ← path↔id cache; NOT committed
        ├── graph_snapshot/             ← *.tar.gz; NOT committed
        └── structured_snapshot/        ← *.tar.gz; NOT committed
```

**Replace in** `docs/vault.md` (lines 355-361):

```markdown
- every table a changed, added or removed **table mapping** matches, and the
  mapped tables of a domain whose **schema** changed (and of its dotted child
  domains). A table that a removed or narrowed mapping no longer covers is
  retracted from the graph.

A table's identity is `(domain, table_name)`, not a SQLite id: `.meta.json`
carries no machine-local id at all — neither the table's own nor any child
```

**with:**

```markdown
- every table a changed, added or removed **table mapping** matches, and the
  mapped tables of a domain whose **schema** changed (and of its dotted child
  domains). A table that a removed or narrowed mapping no longer covers is
  retracted from the graph;
- an **`artmind update`** — each confirmed draft is a staging folder,
  `kg/<domain>/update__<session>__<draft>/`, replayed like any document folder
  (as a `UserChat`, not a `Document`); a deleted one is retracted;
- every **same-as group** added, removed or changed in `.artmind/same_as.yaml`:
  exactly its members are rebuilt, with the groups as committed at `HEAD`;
- every **curation record** added, changed or deleted under
  `.artmind/data/curation/` (see "Curation that travels" below).

A table's identity is `(domain, table_name)`, not a SQLite id: `.meta.json`
carries no machine-local id at all — neither the table's own nor any child
```

**Replace in** `docs/vault.md` (lines 372-377):

```markdown
instead — documented, not built into artmind. Design:
`docs/superpowers/specs/2026-09-26-vault-git-transport-design.md`.

#### Sync bookmarks, fingerprints, and the two topologies

A bookmark means "this store reflects the vault's commits up to X", and each
```

**with:**

```markdown
instead — documented, not built into artmind. Design:
`docs/superpowers/specs/2026-09-26-vault-git-transport-design.md`.

#### Curation that travels

The graph must be rebuildable from the vault alone: a machine with its own
Neo4j never sees a write that exists only in another machine's graph. So
every curation write is also a vault file:

| Curation | Vault file | Written by |
|---|---|---|
| a fact added with `artmind update` | `kg/<domain>/update__<session>__<draft>/` (a staging folder) | `update confirm`; `update retract` deletes it |
| a detected conflict, and its resolution | `curation/conflicts/<id>.json` | `ingest detect-conflicts`, `ingest resolve-conflict` |
| a same-as group | `.artmind/same_as.yaml` | `sameas approve`, or you |

Curation records are **one file per record**, never one shared file: every
path under `.artmind/data/` merges as a whole file, so a shared file would
make any two machines' curation a git conflict. A conflict's id is
deterministic, so two machines detecting the same conflict name the same
file; re-detecting a conflict whose file exists never rewrites it (a
resolved conflict is not reopened). If two machines both detect one before
either pulls, git reports a conflict on that one file — keep either side
(`git checkout --ours` or `--theirs` on it), it is the same conflict.

Same-as **proposals** (`sameas propose`, `ingest refine-graph`, and the
adjudicator's "same entity" verdicts) stay on the machine that made them: they
are a review queue, and an approved group travels as `same_as.yaml`.

#### Sync bookmarks, fingerprints, and the two topologies

A bookmark means "this store reflects the vault's commits up to X", and each
```

**Replace in** `docs/vault.md` (lines 424-432):

```markdown
replay everything committed.

`artmind vault status` shows HEAD, both bookmarks, how many documents and
tables each store is behind (after the fingerprint check), a merge in
progress, and unresolved conflicts under `.artmind/`; `--compact` gives the
same as JSON. After a successful `artmind query ...` in a vault this machine
has synced, a line such as

    artmind: graph is 3 docs / 1 tables behind the vault — run `artmind vault sync`
```

**with:**

```markdown
replay everything committed.

`artmind vault status` shows HEAD, both bookmarks, how many documents and
tables each store is behind (after the fingerprint check) — and, for the
graph, how many curation records and same-as groups — a merge in progress,
and unresolved conflicts under `.artmind/`; `--compact` gives the same as
JSON. After a successful `artmind query ...` in a vault this machine
has synced, a line such as

    artmind: graph is 3 docs / 1 tables behind the vault — run `artmind vault sync`
```

- [ ] **Step 2: Full suite**

Run: `uv run --group dev pytest test/ -q`
Expected: `2519 passed, 14 skipped` (the counts at the end of this plan's verification run).

- [ ] **Step 3: CLI help**

Run each, in-process so no stale daemon answers (CLAUDE.md §1):

```bash
ARTMIND_NO_PROXY=1 uv run artmind vault sync --help
ARTMIND_NO_PROXY=1 uv run artmind update --help
ARTMIND_NO_PROXY=1 uv run artmind update retract --help
just dev-cli-help | grep -n "retract"
```

Expected: `vault sync --help` includes "Curation travels the same way"; `update --help` lists `confirm`, `draft`, `export`, `history`, `retract`; `update retract --help` shows `--session` (required), `--draft`, `--compact`.

- [ ] **Step 4: Commit**

```bash
git add artmind/cli.py artmind/skills/artmind-curate/SKILL.md artmind/skills/artmind-update/SKILL.md artmind/vault_sync.py docs/superpowers/specs/2026-09-26-vault-git-transport-design.md docs/vault.md
git commit -m "docs(vault-sync): curation travels -- vault.md, vault sync docstring, update and curate skills, spec §15 A9"
```

---

## Live verification (user)

Hermetic tests cannot see a real Neo4j (CLAUDE.md §3). Run these against your **local** Neo4j, in throwaway vaults, after this plan is merged and installed. Everything runs in-process (`ARTMIND_NO_PROXY=1`), so a stale `serve` daemon cannot answer with old code. `update draft` and `ingest detect-conflicts` call your LLM (machine-level `~/.artmind/config.env`); nothing else here does.

Two machines, each with **its own** Neo4j database (the topology this plan is for): create two databases that hold nothing you need — in Neo4j Browser, `:use system`, `CREATE DATABASE planca;` and `CREATE DATABASE plancb;` (Desktop/Enterprise). On Community edition run two throwaway servers instead: `docker run -d --name planc-a -p 7688:7687 -e NEO4J_AUTH=neo4j/plancpass neo4j:5` and `docker run -d --name planc-b -p 7689:7687 -e NEO4J_AUTH=neo4j/plancpass neo4j:5`, with URIs `neo4j://127.0.0.1:7688` / `:7689` and database `neo4j`.

### 0. Install and set up two clones

```bash
cd ~/projects/artmind9 && just dev-install
export ARTMIND_NO_PROXY=1
export LIVE=$HOME/planc-live && rm -rf "$LIVE" && mkdir -p "$LIVE" && cd "$LIVE"
git init -q --bare --initial-branch=main remote.git
git clone -q remote.git A && cd "$LIVE/A" && git config user.email a@example.com && git config user.name A
artmind init && $EDITOR .artmind/config.env     # point at planca
artmind setup                                    # constraints include user_chat_history_id
mkdir -p notes && printf '# Acme\n\nAcme Bank is headquartered in Pune. Its CEO is Priya Rao.\n' > notes/acme.md
printf '# Acme risk\n\nAcme Bank reports to the Reserve Bank. Its CEO is Rahul Rao.\n' > notes/acme-risk.md
artmind ingest sync notes/acme.md --domain banking.organization
artmind ingest sync notes/acme-risk.md --domain banking.risk_governance
git add -A && git commit -qm "A: acme" && git push -q origin HEAD:main
artmind vault sync --bootstrapSynced --compact
cd "$LIVE" && git clone -q remote.git B && cd "$LIVE/B" && git config user.email b@example.com && git config user.name B
artmind init && $EDITOR .artmind/config.env     # point at plancb
artmind setup
artmind vault sync --bootstrapEmpty --compact    # B's own graph now has both notes
```

Obsidian Git is simulated with plain git: `git add -A && git commit -qm MSG && git pull -q --no-rebase origin main && git push -q origin HEAD:main`.

### 1. An `artmind update` travels

```bash
cd "$LIVE/A"
artmind update draft --domain banking.organization --text "Priya Rao chairs the Acme Bank risk committee."
#   note session_id; build resolutions from candidates_per_entity (link Priya Rao / Acme Bank to their node_ids)
artmind update confirm --session <session_id> --resolutions '<json>'
#   output has staging_dir = .../.artmind/data/kg/banking.organization/update__<session_id>__<draft_id>
ls .artmind/data/kg/banking.organization/update__*/   # document.json chunks.json observations.json relationships.json
git add -A && git commit -qm "A: update" && git pull -q --no-rebase origin main && git push -q origin HEAD:main
artmind vault status                            # graph current: A's graph already carries the folder's fingerprint (on its UserChat)
artmind vault sync --compact                    # "unchanged":1 -- nothing replayed on A
cd "$LIVE/B" && git pull -q --no-rebase origin main
artmind vault status                            # graph: 1 docs / 0 tables behind
artmind vault sync --compact                    # "replayed":1
```

Cypher on `plancb`:

```cypher
MATCH (c:UserChat) RETURN c.id, c.raw_text, c.fingerprint IS NOT NULL AS fp, c.embedding IS NOT NULL AS embedded;
MATCH (o:Observation)-[:EXTRACTED_FROM]->(c:UserChat) RETURN c.id, o.canonical_name, o.key;
```

One row `update:<session>:<draft>` with `fp` and `embedded` true; the same observations (and keys) as on `planca`. Run the second query on `planca` too and compare.

Retract it on B and follow on A:

```bash
cd "$LIVE/B"
artmind update retract --session <session_id> --compact   # "No such update session": the session lives on A's registry
cd "$LIVE/A" && artmind update retract --session <session_id> --compact    # retracted, folder removed
git add -A && git commit -qm "A: retract" && git pull -q --no-rebase origin main && git push -q origin HEAD:main
cd "$LIVE/B" && git pull -q --no-rebase origin main && artmind vault sync --compact   # "retracted":1
```

`MATCH (c:UserChatHistory) RETURN c.id` on both databases now returns the chat; `MATCH (c:UserChat) RETURN count(c)` is 0.

### 2. A conflict and its resolution travel

```bash
cd "$LIVE/A"
artmind ingest detect-conflicts --domain banking.organization --domain banking.risk_governance --compact
ls .artmind/data/curation/conflicts/            # one <id>.json per materialized conflict
artmind ingest resolve-conflict <id> --status resolved --reason "board minutes, 2026" --compact
grep -n '"status"' .artmind/data/curation/conflicts/<id>.json   # "resolved"
git add -A && git commit -qm "A: conflicts" && git pull -q --no-rebase origin main && git push -q origin HEAD:main
cd "$LIVE/B" && git pull -q --no-rebase origin main
artmind vault status        # "... / N curation records behind"
artmind vault sync --compact   # "curation":{"conflicts":{"apply":N,"remove":0}}
artmind query graph conflicts --domain banking.organization --domain banking.risk_governance --status all --compact   # same ids, the resolved one resolved
```

Re-detect on B — the resolved conflict must stay resolved and its file must not change:

```bash
artmind ingest detect-conflicts --domain banking.organization --domain banking.risk_governance --compact
git status --short .artmind/data/curation/      # nothing
```

(If detection finds no conflict for your two notes — it pairs same-class entities across the two domains by embedding similarity — edit `acme-risk.md` so the two CEO claims clash more explicitly, re-ingest it, and re-run.)

### 3. A `same_as.yaml` edit travels, read from HEAD

```bash
cd "$LIVE/A"
cat > .artmind/same_as.yaml <<'YAML'
groups:
  - canonical: "<key 1>"
    members:
      - "<key 1>"
      - "<key 2>"
YAML
```

(`<key 1>`/`<key 2>`: the two Acme Bank `key`s `MATCH (e:Entity) WHERE e.name =~ '(?i)acme.*' RETURN e.key, e._domain` shows on `planca`, one per domain.)

```bash
artmind projection rebuild --compact           # A applies its own edit
git add -A && git commit -qm "A: same-as" && git pull -q --no-rebase origin main && git push -q origin HEAD:main
cd "$LIVE/B" && git pull -q --no-rebase origin main
printf 'groups: []\n' > .artmind/same_as.yaml  # an uncommitted edit that must NOT reach the graph
artmind vault status                           # "... / 1 same-as groups behind"
artmind vault sync --compact                   # "same_as_groups":1,"same_as_keys":2
git checkout -- .artmind/same_as.yaml
```

On `plancb`: `MATCH (a:Entity)-[:SAME_AS]-(b:Entity) RETURN a.key, b.key` shows the link (cross-domain members are linked, not merged) — sync used the committed groups, not the emptied working-tree file.

### Clean up

```bash
rm -rf "$LIVE"
```

Then `DROP DATABASE planca; DROP DATABASE plancb;` (from `:use system`) or `docker rm -f planc-a planc-b`.

---

## Self-review

### Scope → tasks

| Scope item | Task(s) |
|---|---|
| 1. `update confirm` writes `kg/<domain>/update__…/` atomically (`write_dir_atomic` via `write_staging`) | 7 |
| 1. keeps writing Neo4j immediately | 7 (the commit runs inside `confirm`) |
| 1. records the fingerprint so the local apply skips it | 6 (on `:UserChat`), 7 (test), Task 7's sync test for the shared-graph skip |
| 1. replay produces the same graph state; direct write vs `_commit_document_tx` compared and unified | decision 1; 6, 7 (`test_replaying_the_folder_writes_exactly_what_confirm_wrote`) |
| 1. deleting or rejecting an update retracts it | 6 (`retract_document` + `:UserChatHistory`), 7 (`test_a_deleted_update_folder_retracts_its_user_chat`), 8 (`update retract`) |
| 1. deterministic, collision-free doc id | decision 2; 7 |
| 2. conflict records persisted, one file per conflict | 3, 4 |
| 2. `materialize` and `resolve_conflict` write/update the file | 4 |
| 2. new sync track: MERGE from the file; deleted files remove | 5 |
| 2. deterministic ids MERGE onto the same record | 3 (`conflicts.conflict_id` reused), 4 (re-detection test) |
| 2. status conflict between machines | decisions 6, 7; 4 (re-detection never reopens), 10 (docs) |
| 3. track D diffs `same_as.yaml` base..head, rebuilds exactly the changed groups' keys | 2 |
| 3. rebuild reads `same_as.yaml` at `head`, never the working tree; `_rebuild_in_batches` fixed | 1, 2 |
| 3. `SameAsProposal` stays machine-local, documented | decision 10; 10 |
| 4. fingerprints/bookmarks cover the new inputs; staleness and `vault status` count them | 5 (curation fingerprint skip), 6 (UserChat fingerprints), 9 |
| 5. dry-run parity, domain scoping without advancing, per-store bookmarks, ancestor refusal intact | 2, 5 (dry-run and domain tests per track); the existing suite, unchanged, keeps passing after every task |
| 6. docs, `vault sync` docstring, update and curate skills; final suite and CLI help | 10 |

### Placeholder scan

No "TBD", "similar to Task N", or step without its code. Every replaced block is quoted exactly and was matched by the verification script (below); every test step names the exact command and the observed result.

### Type and name consistency

`same_as.parse_groups` (1) → `vault_sync.same_as_groups_at` (2). `table2graph._rebuild_in_batches(keys, groups=None)` (1) → `sync()` (2), `update.retract_update` (8). `SyncPlan.same_as_keys`/`same_as_groups` (2), `SyncPlan.curation` (5) → `_graph_pending` (9). `curation_records`: `Kind(name, phase, apply, remove, read_fingerprints, domains)`, `kinds()`, `record_path`, `serialize`, `fingerprint`, `write_record`, `read_record`, `parse`, `delete_record` (3) → `conflicts` (4), `vault_sync` (5, 9). `conflict_records`: `NAME`, `record_from_detection`, `record_from_graph`, `apply(tx, record, fingerprint)`, `remove(tx, record)`, `read_fingerprints(ids, *, timeout)`, `domains`, `KIND` (3) → 4, 5. `ingest.USER_CHAT_SOURCE_KIND` (6), `ingest.UPDATE_FOLDER_PREFIX` (7) → `update`, `vault_sync`. `sync_state.set_document_fingerprint(..., label=)` (6). `update.user_chat_id`, `update.update_folder`, `write_user_chat(..., draft_id=)`, `embed_user_chats`, `_chat_relationships` (7), `retract_update` (8). `vault_sync.curation_phrase` (9) → `cli._echo_sync_status`. Result keys `same_as_groups`, `same_as_keys`, `curation`, `curation_unchanged`, `staging_dir` are the same in code, tests and docs.

### Ordering and green suite

Each task leaves `uv run --group dev pytest test/ -q` green; the verification record has the count after each. Tasks 4, 7 and 9 change existing tests' expectations, each in the same task: one lookup in `test_conflict_resolution.py` (4), five Cypher filters in `test_update.py` (7), two whole-report comparisons in `test_vault_sync.py` (9); Task 6 adds one label to `test_graph_snapshot.py`'s expected set.

### Design decisions the spec did not settle (for review)

1. **`update` is routed through `_commit_document_tx`; the `:UserChat` is kept.** The two writes were not equivalent: the direct write created `:UserChat` + observations with `SET o = $props` and `EXTRACTED_FROM`→`UserChat`, never demoted a prior version (a retried confirm would accrete), recorded no fingerprint, and rebuilt only its own keys — not the same-as groups touching them, which `projection.rebuild` needs to fold a group correctly. `_commit_document_tx` does all of that but writes a `:Document` with `:DocChunk`s. Replacing the `:UserChat` with a Document would change what `query vector-text` (which searches `:UserChat`), the full-text index `user_chat_text_ft`, `update export`, the structural census and the skills describe. So the commit transaction gained one branch, keyed by `document.json`'s `source_kind`: the provenance node is a `:UserChat` and observations link to it; everything else is shared. The staged relationships name their endpoint observations by id, and the edge id (`relation_observation_id(chat_id, …)`) is the one the direct writer used.
2. **One folder per confirmed draft**: `update__<session_id>__<draft_id>`, not `update__<session_id>` (the spec's wording) — a session can confirm several drafts, and one folder per session would hold several documents. The id `update:<session_id>:<draft_id>` is deterministic (a retry reuses it) and cannot collide across machines (the session's uuid4 lives in one registry). `write_user_chat` called without a `draft_id` (only tests do) falls back to a random 12-hex suffix.
3. **"Rejecting" an update = `artmind update retract`**: a draft that is never confirmed writes nothing, so there is nothing to reject; a confirmed one is withdrawn by retracting its folder(s). The graph goes first so a failure leaves the folder for a retry; the folder deletion is what travels. The session lives on one machine's registry, so `update retract` runs where the session was created; any machine can also delete the folder by hand and let `vault sync` retract it.
4. **One file per curation record**, `.artmind/data/curation/<kind>/<id>.json`, written with a temp file + `os.replace` (the temp name ends `.artmind-tmp`; it is a file, so R4's `*.artmind-tmp/` directory rule does not ignore it — a crash can leave one, the curation track only reads `*.json`, and the next write of that record removes it).
5. **A generic record framework rather than a conflicts-only track**, so Plan C2 adds supersessions, retirements and syntheses as kinds without touching `vault sync`. `Kind.remove` receives the record as it last existed (its bytes at the sync's base), which C2's supersession removal needs.
6. **The vault's record wins over re-detection**; a pre-record conflict keeps its node's fields; applying SETs every field from the record.
7. **Two machines writing the same record before pulling** is a git conflict on that file; `vault sync` refuses until it is resolved (either side). Not automated here — `vault resolve` (phase 5) reports curation files and leaves them to the user, per spec R7.
8. **Track D rebuilds with HEAD's groups and whole groups.** A group counts under `--domain` when any member is in scope, and is then rebuilt whole. Reordering a group's members is not a change; a new canonical is. `:ProjectionState`'s `same_as_hash` drift flag is not touched by a sync (as with `sameas approve`, only a full `projection rebuild` clears it).
9. **Pending counts**: per decision 9 above. A changed same-as group always counts until this machine syncs, even on a shared graph the other machine already rebuilt — there is no per-group fingerprint to compare. Accepted: the rebuild is idempotent and cheap.
10. **Same-as proposals stay local** (spec §14 A6), and so do the review-queue outputs of `ingest refine-graph` and `detect-conflicts`' same-entity verdicts.

### Accepted residual risk

- **A conflict record naming an entity this graph does not have yet** creates the `:Conflict` node without its `CONFLICTS_WITH` edges; a later sync that does not touch the record will not add them. Re-running detection (or resolving the conflict, which re-applies the record) attaches them.
- **A retried `update confirm` of the same draft** rewrites the folder with a new `created_at`, so its fingerprint changes and a shared graph replays it once more (idempotently).
- **Existing adjudicator conflicts** get a record file only when re-detected or resolved; a one-off export of every pre-record conflict is not part of this plan.

## Verification record

- Worktree: `git worktree add --detach <scratchpad>/verifyC e9f7a9f` (the tip of `vault-sync-cde`). A script parsed this file and applied, in document order, every **Create** and **Replace in** … **with:** block — a replacement must match its file exactly once — running `uv run --group dev pytest test/ -q` after each task and committing it.
- Baseline at `e9f7a9f`: `2440 passed, 14 skipped`.
- Results after each task (all `… passed, 14 skipped`): Task 1: `2443`; Task 2: `2456`; Task 3: `2476`; Task 4: `2482`; Task 5: `2490`; Task 6: `2498`; Task 7: `2508`; Task 8: `2514`; Task 9: `2519`; Task 10: `2519`.
- The applied tree is identical to branch `planC-applied` (`git diff --quiet planC-applied HEAD` → exit 0). That branch holds the same code as one commit per task on top of `vault-sync-cde`, kept for Plan C2 and later planners.
- Expected-failure claims: for every task with tests, the task's non-test files were reverted to the previous task's state (new modules deleted) and the task's test files re-run; each Step 2 states what that produced, including the tests that pass on arrival (Tasks 4, 7, 9 say which). Re-checked on the plan-applied worktree for Tasks 2, 3, 4, 5, 6, 7, 8 and 9: `13 failed, 135 passed`; collection error (`ImportError: cannot import name 'conflict_records'`); `6 failed, 15 passed`; `8 failed, 148 passed`; `8 failed, 43 passed`; `14 failed, 189 passed`; `6 failed`; `6 failed, 158 passed` — as stated.
- The verification worktrees were removed afterwards; only this plan file (and Plan C2's) was committed to `vault-sync-cde`.
