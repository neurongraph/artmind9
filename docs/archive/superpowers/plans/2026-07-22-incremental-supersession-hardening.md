# Incremental Supersession Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the three remaining gaps from the incremental-ingestion review (`docs/INCREMENTAL_INGESTION.md` §8): auto-infer supersession for same-title-family document versions, make entity properties version-aware when a document supersedes another, and default the query skill to a current-truth view.

**Architecture:** All three changes extend the existing commit-time "self-asserted truth" hooks — no refine-pipeline involvement. Task 1 adds a schema-gated third detection route to `detect_supersession` (title-family + `valid_from` ordering). Task 2 adds a post-supersession property re-assertion step to `commit_to_graph` so the superseding document's extracted values overwrite the accretive merge. Task 3 is a skill-text change making `--asOf today` the default query posture.

**Tech Stack:** Python 3.14 / uv, Click CLI, Neo4j via `neo4j` driver, pytest with mocked Neo4j sessions (the suite runs hermetically — no live Neo4j, no network). Run tests with `just dev-test` (= `uv run --group dev pytest test/ -v`).

---

## Context you must know before starting

Read `CLAUDE.md` at the repo root first. Key facts used below:

- **Prior state:** the §6.2 bug (entity id mismatch in `normalize_ingested_document`) is ALREADY FIXED in the working tree — `artmind/temporal.py` now matches entities by `(name, entity_class, domain)` and counts only matched nodes. Do not re-fix it; Task 2 reuses the same match-key pattern.
- `commit_to_graph` (`artmind/ingest.py`, bottom of file) is the single convergence point for all ingestion routes (sync, async worker, staged `write-to-graph`, `pull-kg`). Hooks there are best-effort: wrapped in try/except, a failure logs a warning but never fails the commit.
- `detect_supersession` (`artmind/temporal.py`, bottom of file) currently recognizes two *explicit* declaration formats (prose notice, metadata-table row). `apply_supersession` is an idempotent `MERGE` keyed on scope; re-applying the same pair with the same effective date is harmless.
- `_title_stem` (`artmind/temporal.py`) reduces `interest_rate_schedule_2026_03.md` and `policy_complaints_v3.md` to their family stems by repeatedly stripping trailing `_v?<digits>` suffixes. Timestamp rename suffixes (`_20260722_153000`) strip the same way.
- `_upsert_entity` (`artmind/ingest.py`) merges entity properties accretively via `_merge_prop_value`: lists union, strings become `"old | new"`, numbers/booleans keep the existing value. `_flatten_props` (same file) converts a props dict to Neo4j-safe values and drops empties.
- Schema temporal blocks: `load_schema(domain)` (`artmind/temporal.py`) deep-merges a dotted child's `temporal:` block over its parent's — but **only** the keys `defaults`, `relative_anchor`, `document`, `entities` survive the merge (`_deep_merge_temporal`). Any new behavior flag MUST therefore live under `temporal.defaults:`, not at the top of `temporal:`.
- Tests mock Neo4j with small `FakeSession` classes and `monkeypatch` — copy the style already in `test/test_supersession.py` and `test/test_ingest_hooks.py`.
- Domain schema YAMLs in `artmind/domains/schemas/` are seeded to the run folder (`~/.artmind/domains/schemas/`) **only when absent** — editing the packaged YAML does not update an existing run folder. Flag this to the operator at the end (do not edit `~/.artmind` yourself).

---

### Task 1: Schema-gated title-family supersession inference

When a domain schema opts in (`temporal.defaults.supersede_on_title_family: true`), `detect_supersession` additionally infers a version chain among documents sharing a title family, ordered by canonical `valid_from` — closing the "plain re-ingest with no notice" gap. Off by default because families like `meeting_notes_2026_01/_02` are series, not versions; only the schema author knows which semantics a domain has. Explicit notices always take precedence; ties and ambiguity are skipped, never guessed.

**Files:**
- Modify: `artmind/temporal.py` (docs query, new helper `_infer_family_supersessions`, family pass at end of `detect_supersession`, docstring)
- Modify: `artmind/cli.py:1065-1070` (`detect-supersession` help text)
- Modify: `artmind/domains/schemas/banking.policy_schema.yaml` (enable the flag as exemplar)
- Test: `test/test_supersession.py`

- [ ] **Step 1: Write the failing tests**

Append to `test/test_supersession.py` (it already imports `artmind.temporal as t` and `MagicMock`/`patch`/`contextmanager` at the top):

```python
# ── title-family inference (schema-gated) ─────────────────────────────────────


class _FamilySession:
    """FakeSession whose docs query returns the given rows."""

    def __init__(self, docs):
        self._docs = docs

    def run(self, *a, **k):
        docs = self._docs

        class _Result:
            def data(self):
                return docs

        return _Result()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _family_gate_on(domain):
    return {"temporal": {"defaults": {"supersede_on_title_family": True}}}


def test_family_inference_links_dated_siblings_without_notice(monkeypatch):
    # Neither document carries a notice; the schema gate is on; valid_from
    # ordering alone infers the chain. effective = the newer doc's valid_from.
    docs = [
        {"id": "irs-03", "name": "interest_rate_schedule_2026_03.md", "version": None, "valid_from": "2026-03-01"},
        {"id": "irs-02", "name": "interest_rate_schedule_2026_02.md", "version": None, "valid_from": "2026-02-01"},
    ]
    applied = []
    monkeypatch.setattr(t, "neo4j_session", lambda: _FamilySession(docs))
    monkeypatch.setattr(t, "_read_doc_body", lambda name: "plain body, no notice", raising=False)
    monkeypatch.setattr(t, "load_schema", _family_gate_on)
    monkeypatch.setattr(
        t, "apply_supersession",
        lambda newer, older, scope, eff, detected_by: applied.append((newer, older, eff, detected_by)),
    )

    report = t.detect_supersession("banking.reference")

    assert applied == [("irs-03", "irs-02", "2026-03-01", "title_family")]
    assert report["applied"] == [
        {"newer": "irs-03", "older": "irs-02", "effective": "2026-03-01", "detected_by": "title_family"}
    ]


def test_family_inference_off_by_default(monkeypatch):
    # No schema flag → no inference, even with perfectly ordered siblings.
    docs = [
        {"id": "a", "name": "notes_2026_01.md", "version": None, "valid_from": "2026-01-05"},
        {"id": "b", "name": "notes_2026_02.md", "version": None, "valid_from": "2026-02-05"},
    ]
    applied = []
    monkeypatch.setattr(t, "neo4j_session", lambda: _FamilySession(docs))
    monkeypatch.setattr(t, "_read_doc_body", lambda name: "plain body", raising=False)
    monkeypatch.setattr(t, "load_schema", lambda domain: {})
    monkeypatch.setattr(
        t, "apply_supersession",
        lambda *a, **k: applied.append(a),
    )

    report = t.detect_supersession("meetings")

    assert applied == []
    assert report["applied"] == []


def test_family_inference_skips_valid_from_ties_and_undated_docs(monkeypatch):
    docs = [
        {"id": "a", "name": "sched_v1.md", "version": None, "valid_from": "2026-02-01"},
        {"id": "b", "name": "sched_v2.md", "version": None, "valid_from": "2026-02-01"},  # tie with a
        {"id": "c", "name": "sched_v3.md", "version": None, "valid_from": None},           # undated
    ]
    applied = []
    monkeypatch.setattr(t, "neo4j_session", lambda: _FamilySession(docs))
    monkeypatch.setattr(t, "_read_doc_body", lambda name: "plain body", raising=False)
    monkeypatch.setattr(t, "load_schema", _family_gate_on)
    monkeypatch.setattr(t, "apply_supersession", lambda *a, **k: applied.append(a))

    report = t.detect_supersession("banking.reference")

    assert applied == []
    assert report["applied"] == []


def test_family_inference_chains_consecutive_pairs(monkeypatch):
    docs = [
        {"id": "v1", "name": "sched.md", "version": None, "valid_from": "2026-01-01"},
        {"id": "v3", "name": "sched_v3.md", "version": None, "valid_from": "2026-03-01"},
        {"id": "v2", "name": "sched_v2.md", "version": None, "valid_from": "2026-02-01"},
    ]
    applied = []
    monkeypatch.setattr(t, "neo4j_session", lambda: _FamilySession(docs))
    monkeypatch.setattr(t, "_read_doc_body", lambda name: "plain body", raising=False)
    monkeypatch.setattr(t, "load_schema", _family_gate_on)
    monkeypatch.setattr(
        t, "apply_supersession",
        lambda newer, older, scope, eff, detected_by: applied.append((newer, older)),
    )

    t.detect_supersession("banking.reference")

    # Consecutive chain by valid_from: v2 supersedes v1, v3 supersedes v2.
    assert applied == [("v2", "v1"), ("v3", "v2")]


def test_family_inference_respects_only_doc_name(monkeypatch):
    # Commit-time per-document scope: only pairs whose NEWER side is the
    # committing document apply.
    docs = [
        {"id": "v1", "name": "sched.md", "version": None, "valid_from": "2026-01-01"},
        {"id": "v2", "name": "sched_v2.md", "version": None, "valid_from": "2026-02-01"},
        {"id": "v3", "name": "sched_v3.md", "version": None, "valid_from": "2026-03-01"},
    ]
    applied = []
    monkeypatch.setattr(t, "neo4j_session", lambda: _FamilySession(docs))
    monkeypatch.setattr(t, "_read_doc_body", lambda name: "plain body", raising=False)
    monkeypatch.setattr(t, "load_schema", _family_gate_on)
    monkeypatch.setattr(
        t, "apply_supersession",
        lambda newer, older, scope, eff, detected_by: applied.append((newer, older)),
    )

    t.detect_supersession("banking.reference", only_doc_name="sched_v2.md")

    assert applied == [("v2", "v1")]


def test_family_inference_defers_to_explicit_notice(monkeypatch):
    # The newer doc self-declares via a metadata-table row. The notice pass
    # applies first (detected_by="notice"); the family pass must then skip the
    # same pair rather than re-apply it as "title_family".
    docs = [
        {"id": "irs-01", "name": "interest_rate_schedule_2026.md", "version": "1.0", "valid_from": "2026-01-01"},
        {"id": "irs-02", "name": "interest_rate_schedule_2026_02.md", "version": "1.0", "valid_from": "2026-02-01"},
    ]
    bodies = {
        "interest_rate_schedule_2026.md": "| Supersedes | None |\n",
        "interest_rate_schedule_2026_02.md": (
            "| Effective Date | 2026-02-01 |\n"
            "| Supersedes | [[interest_rate_schedule_2026]] |\n"
        ),
    }
    applied = []
    monkeypatch.setattr(t, "neo4j_session", lambda: _FamilySession(docs))
    monkeypatch.setattr(t, "_read_doc_body", lambda name: bodies[name], raising=False)
    monkeypatch.setattr(t, "load_schema", _family_gate_on)
    monkeypatch.setattr(
        t, "apply_supersession",
        lambda newer, older, scope, eff, detected_by: applied.append((newer, older, detected_by)),
    )

    report = t.detect_supersession("banking.reference")

    assert applied == [("irs-02", "irs-01", "notice")]
    assert len(report["applied"]) == 1
```

- [ ] **Step 2: Run the new tests to verify they fail**

```bash
uv run --group dev pytest test/test_supersession.py -v -k "family_inference"
```

Expected: all 6 FAIL (family inference not implemented; `applied`/`report["applied"]` stay empty, or `load_schema` is never consulted).

- [ ] **Step 3: Implement in `artmind/temporal.py`**

3a. In `detect_supersession`, extend the docs Cypher to also return `valid_from`. Replace:

```python
        docs = session.run(
            "MATCH (d:Document) WHERE d.domain=$domain RETURN d.id AS id, d.name AS name, d.version AS version",
            domain=domain,
        ).data()
```

with:

```python
        docs = session.run(
            "MATCH (d:Document) WHERE d.domain=$domain "
            "RETURN d.id AS id, d.name AS name, d.version AS version, d.valid_from AS valid_from",
            domain=domain,
        ).data()
```

3b. Add a module-level helper directly above `detect_supersession`:

```python
def _infer_family_supersessions(
    docs: list[dict], only_doc_name: str | None
) -> list[tuple[dict, dict, str]]:
    """Infer supersession pairs among same-title-family documents by valid_from.

    Only documents carrying a canonical valid_from participate. Members of a
    family are sorted by valid_from and linked as a chain of consecutive pairs
    (each newer document supersedes its immediate predecessor). A tie on
    valid_from is skipped — ambiguity is never guessed. When only_doc_name is
    set, only pairs whose NEWER side is that document are returned (the
    commit-time per-document scope). Returns (newer, older, effective) tuples
    with effective = the newer document's valid_from.
    """
    families: dict[str, list[dict]] = {}
    for d in docs:
        if d.get("valid_from"):
            families.setdefault(_title_stem(d["name"]), []).append(d)
    pairs: list[tuple[dict, dict, str]] = []
    for group in families.values():
        if len(group) < 2:
            continue
        group.sort(key=lambda d: str(d["valid_from"]))
        for older, newer in zip(group, group[1:]):
            if str(older["valid_from"]) == str(newer["valid_from"]):
                continue
            if only_doc_name is not None and newer["name"] != only_doc_name:
                continue
            pairs.append((newer, older, str(newer["valid_from"])))
    return pairs
```

3c. At the very end of `detect_supersession`, insert the family pass between the existing notice loop and `return report`:

```python
    # ── title-family inference (schema-gated third route) ─────────────────────
    # Explicit notices above take precedence: any pair they already applied this
    # run is skipped here. detected_by distinguishes the routes in the report and
    # on the SUPERSEDES edge; notice-derived report entries keep their original
    # shape (no detected_by key).
    defaults = (load_schema(domain).get("temporal") or {}).get("defaults") or {}
    if defaults.get("supersede_on_title_family"):
        applied_pairs = {(a["newer"], a["older"]) for a in report["applied"]}
        for newer, older, effective in _infer_family_supersessions(docs, only_doc_name):
            pair = (newer["id"], older["id"])
            if pair in applied_pairs:
                continue
            applied_pairs.add(pair)
            report["applied"].append(
                {"newer": newer["id"], "older": older["id"],
                 "effective": effective, "detected_by": "title_family"}
            )
            if not dry_run:
                apply_supersession(
                    newer["id"], older["id"], "document", effective,
                    detected_by="title_family",
                )
    return report
```

3d. Extend the `detect_supersession` docstring: after the numbered list of the two notice formats, add:

```
      3. When the domain schema sets temporal.defaults.supersede_on_title_family,
         a version chain is inferred among same-title-family documents ordered by
         canonical valid_from (see _infer_family_supersessions). Off by default:
         dated series (meeting notes, monthly reports) share a title family
         without superseding each other — only the schema author knows which
         semantics a domain has. Explicit notices take precedence per pair.
```

- [ ] **Step 4: Run the full supersession + temporal test files**

```bash
uv run --group dev pytest test/test_supersession.py test/test_temporal.py -v
```

Expected: ALL PASS — the 6 new tests plus every pre-existing test (the pre-existing ones either mock domains whose schema lacks the flag, or supply docs without `valid_from`, so the new pass no-ops for them). If a pre-existing test fails, the family pass is not properly gated — fix the implementation, do not modify those tests.

- [ ] **Step 5: Update the CLI help text** (`artmind/cli.py`, `detect-supersession` command)

Replace:

```python
@click.option("--domain", required=True, help="Domain to scan for explicit Supersession Notice sections")
```

with:

```python
@click.option("--domain", required=True, help="Domain to scan for supersession declarations (notices, metadata rows, and — when the schema enables supersede_on_title_family — title-family version chains)")
```

and replace the command docstring:

```python
    """Scan documents for explicit Supersession Notice sections and apply SUPERSEDES edges."""
```

with:

```python
    """Scan documents for supersession declarations and apply SUPERSEDES edges.

    Recognizes prose "## Supersession Notice" sections, metadata-table
    "| Supersedes | [[doc]] |" rows, and — when the domain schema sets
    temporal.defaults.supersede_on_title_family — inferred version chains among
    same-title-family documents ordered by valid_from.
    """
```

- [ ] **Step 6: Enable the flag in the exemplar schema** (`artmind/domains/schemas/banking.policy_schema.yaml`)

In the `temporal:` block, replace:

```yaml
  defaults:
    valid_from: ingestion_date
    valid_to: null
    superseded_by: null
    time_source: default_ingestion
    valid_from_inferred: true
```

with:

```yaml
  defaults:
    valid_from: ingestion_date
    valid_to: null
    superseded_by: null
    time_source: default_ingestion
    valid_from_inferred: true
    # Documents in this domain are versioned policies: a same-title-family
    # sibling with a newer valid_from IS a newer version. Enables inferred
    # SUPERSEDES chains for re-ingested updates that omit an explicit notice.
    supersede_on_title_family: true
```

- [ ] **Step 7: Run the whole suite**

```bash
uv run --group dev pytest test/ -v
```

Expected: ALL PASS (429+ tests). Note: `test_detect_supersession_ignores_unrelated_doc_sharing_version` exercises domain `banking.policy` with the real schema now carrying the flag — it still passes because its mocked docs have no `valid_from`, so inference yields no pairs. If it fails, your family pass is not skipping undated docs.

- [ ] **Step 8: Commit**

```bash
git add artmind/temporal.py artmind/cli.py artmind/domains/schemas/banking.policy_schema.yaml test/test_supersession.py
git commit -m "feat(temporal): schema-gated title-family supersession inference

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: Re-assert the superseding document's entity properties

`_upsert_entity`'s accretive merge (strings → `"old | new"`, scalars keep the existing value) is right for peer documents but wrong once the committing document is known to supersede a contributor: an updated fee of 6.0 must not lose to the old 5.0, and `effective_date` must not become `"2026-01-15 | 2026-06-01"` (which `parse_iso` resolves to the *older* date). Fix: after `detect_supersession` applies anything for this document, overwrite the merged entities' domain properties with this document's own staged values.

**Files:**
- Modify: `artmind/ingest.py` (new `_reassert_superseding_properties`, wire into `commit_to_graph`)
- Test: `test/test_ingest_hooks.py`

- [ ] **Step 1: Write the failing tests**

Append to `test/test_ingest_hooks.py`:

```python
def test_reassert_superseding_properties_overwrites_by_entity_key(monkeypatch, tmp_path):
    import json
    import artmind.ingest as ing

    (tmp_path / "entities.json").write_text(json.dumps([
        {"id": "c1_e1", "name": "Standard Fee", "entity_class": "FEE", "domain": "banking.reference"},
        {"id": "c1_e2", "name": "No Props", "entity_class": "FEE", "domain": "banking.reference"},
    ]), encoding="utf-8")
    (tmp_path / "properties.json").write_text(json.dumps([
        {"id": "c1_e1", "properties": {"monthly_amount": 6.0, "effective_date": "2026-06-01"}},
    ]), encoding="utf-8")

    runs = []

    class FakeResult:
        def single(self):
            return {"matched": 1}

    class FakeSession:
        def run(self, cypher, **kwargs):
            runs.append((cypher, kwargs))
            return FakeResult()

    class FakeCtx:
        def __enter__(self):
            return FakeSession()

        def __exit__(self, *exc):
            return False

    import artmind.graph_query as gq
    monkeypatch.setattr(gq, "neo4j_session", lambda: FakeCtx())

    out = ing._reassert_superseding_properties(tmp_path, "banking.reference")

    assert out == {"entities_reasserted": 1}
    assert len(runs) == 1  # the props-less entity is skipped entirely
    cypher, kwargs = runs[0]
    # Must match by the same key _upsert_entity merges on — never by the
    # staged chunk-scoped extraction id (graph nodes carry uuid ids).
    assert "{id:" not in cypher
    assert "name:$name" in cypher and "entity_class:$ec" in cypher and "domain:$domain" in cypher
    assert "SET n += $props" in cypher
    assert kwargs["name"] == "Standard Fee"
    assert kwargs["ec"] == "FEE"
    assert kwargs["domain"] == "banking.reference"
    assert kwargs["props"]["monthly_amount"] == 6.0
    assert kwargs["props"]["effective_date"] == "2026-06-01"


def test_commit_to_graph_reasserts_props_only_when_supersession_applied(monkeypatch, tmp_path):
    import json
    import artmind.ingest as ing
    import artmind.temporal as temporal

    (tmp_path / "document.json").write_text(json.dumps({"id": "d1", "name": "f.md"}), encoding="utf-8")
    monkeypatch.setattr(ing, "write_to_graph", lambda p: True)
    monkeypatch.setattr(temporal, "normalize_ingested_document", lambda p, d: None)

    reasserts = []
    monkeypatch.setattr(
        ing, "_reassert_superseding_properties",
        lambda p, d: reasserts.append((p, d)) or {"entities_reasserted": 1},
    )

    # Supersession applied something → re-assert runs.
    monkeypatch.setattr(
        temporal, "detect_supersession",
        lambda d, only_doc_name=None: {"applied": [{"newer": "d1", "older": "d0"}]},
    )
    assert ing.commit_to_graph(tmp_path, "mydomain") is True
    assert reasserts == [(tmp_path, "mydomain")]

    # Nothing applied → no re-assert.
    reasserts.clear()
    monkeypatch.setattr(
        temporal, "detect_supersession",
        lambda d, only_doc_name=None: {"applied": []},
    )
    assert ing.commit_to_graph(tmp_path, "mydomain") is True
    assert reasserts == []
```

- [ ] **Step 2: Run the new tests to verify they fail**

```bash
uv run --group dev pytest test/test_ingest_hooks.py -v -k "reassert"
```

Expected: FAIL with `AttributeError: ... has no attribute '_reassert_superseding_properties'`.

- [ ] **Step 3: Implement in `artmind/ingest.py`**

3a. Add the function directly above `commit_to_graph`:

```python
def _reassert_superseding_properties(doc_kg_dir: Path, domain: str) -> dict:
    """Overwrite merged entity properties with the superseding document's own values.

    _upsert_entity's merge is accretive — strings become "old | new" and
    numbers/booleans keep the existing value — which is right for peer documents
    but wrong once THIS document is known to supersede a contributor of those
    values. Called from commit_to_graph only after detect_supersession applied a
    SUPERSEDES edge for this document. Scoped to the domain properties this
    document itself asserts (properties.json); name/description/aliases/context
    live in entities.json and keep their accretive behaviour (consolidation's
    job). Matches by (name, entity_class, domain), the same key _upsert_entity
    merges on. Idempotent.
    """
    try:
        entities = json.loads((doc_kg_dir / "entities.json").read_text(encoding="utf-8"))
        properties_path = doc_kg_dir / "properties.json"
        properties_list = (
            json.loads(properties_path.read_text(encoding="utf-8")) if properties_path.exists() else []
        )
    except Exception as e:
        logger.warning("reassert_superseding_properties: could not load staged JSON: {}", e)
        return {"entities_reasserted": 0}

    props_by_id = {p["id"]: p.get("properties", {}) for p in properties_list}
    from artmind.graph_query import neo4j_session

    reasserted = 0
    with neo4j_session() as session:
        for e in entities:
            props = _flatten_props(props_by_id.get(e["id"], {}))
            if not props:
                continue
            rec = session.run(
                "MATCH (n:Entity {name:$name, entity_class:$ec, domain:$domain}) "
                "SET n += $props RETURN count(n) AS matched",
                name=e["name"],
                ec=e["entity_class"],
                domain=e.get("domain") or domain,
                props=props,
            ).single()
            reasserted += rec["matched"] if rec else 0
    if reasserted:
        logger.info(
            "reassert_superseding_properties: {} entity node(s) updated from {}",
            reasserted, doc_kg_dir.name,
        )
    return {"entities_reasserted": reasserted}
```

3b. In `commit_to_graph`, replace hook 2:

```python
    # 2. Supersession from this document's own notice (must follow temporal so
    #    canonical dates/version exist). Scoped to just this document.
    try:
        from artmind.temporal import detect_supersession
        document = json.loads((doc_kg_dir / "document.json").read_text(encoding="utf-8"))
        detect_supersession(domain, only_doc_name=document.get("name"))
    except Exception as e:
        logger.warning("commit_to_graph: supersession hook failed for {}: {}", doc_kg_dir, e)
```

with:

```python
    # 2. Supersession from this document's own declaration (must follow temporal
    #    so canonical dates/version exist). Scoped to just this document. When a
    #    SUPERSEDES edge was applied, re-assert this document's extracted entity
    #    properties over the accretive merge — the superseding version's values
    #    win (see _reassert_superseding_properties).
    try:
        from artmind.temporal import detect_supersession
        document = json.loads((doc_kg_dir / "document.json").read_text(encoding="utf-8"))
        sup_report = detect_supersession(domain, only_doc_name=document.get("name"))
        if (sup_report or {}).get("applied"):
            _reassert_superseding_properties(doc_kg_dir, domain)
    except Exception as e:
        logger.warning("commit_to_graph: supersession hook failed for {}: {}", doc_kg_dir, e)
```

The `(sup_report or {})` guard matters: existing hook-order tests monkeypatch `detect_supersession` with lambdas returning `None`, and they must keep passing unchanged.

- [ ] **Step 4: Run the hook tests, then the whole suite**

```bash
uv run --group dev pytest test/test_ingest_hooks.py -v
```

Expected: ALL PASS — the 2 new tests plus every pre-existing hook-order/stage-only test, unmodified.

```bash
uv run --group dev pytest test/ -v
```

Expected: ALL PASS.

- [ ] **Step 5: Commit**

```bash
git add artmind/ingest.py test/test_ingest_hooks.py
git commit -m "feat(ingest): re-assert superseding document's entity properties at commit

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: Default the query skill to the current-truth view

Without `--asOf`, no temporal filter is emitted and superseded documents/chunks surface in every query, including vector search. The query skill currently frames `--asOf today` as an add-on for present-tense questions; flip it to the default posture.

**Files:**
- Modify: `artmind/skills/artmind-query/SKILL.md` (the `--asOf` bullet, around line 136)

**Editing rule (from CLAUDE.md):** edit ONLY `artmind/skills/artmind-query/SKILL.md` — never `.claude/skills/` (a symlink into it) and never `~/.artmind/` (overwritten by `init`).

- [ ] **Step 1: Replace the `--asOf` bullet**

In `artmind/skills/artmind-query/SKILL.md`, replace:

```
- Add `--asOf today` for present-tense questions ("who can approve…"); it resolves to
  the current date (any ISO date works for historical questions). Untimed knowledge is
  always visible. EXCEPTION: pattern5 and pattern10 cannot currency-scope their results
  and ignore `--asOf` — their JSON then carries `asOf_ignored: true`; judge currency
  yourself from the returned `valid_to`/`superseded_by` fields.
```

with:

```
- **Default to `--asOf today` on every retrieval** — without it there is NO temporal
  filter, and superseded documents and chunks surface alongside current ones. Omit it
  (or pass a past ISO date, e.g. `--asOf 2026-01`) only when the question is explicitly
  historical: "what did the policy say in January", "history of…", "previous version",
  "what changed". Untimed knowledge is always visible either way. EXCEPTION: pattern5
  and pattern10 cannot currency-scope their results and ignore `--asOf` — their JSON
  then carries `asOf_ignored: true`; judge currency yourself from the returned
  `valid_to`/`superseded_by` fields.
```

- [ ] **Step 2: Verify no other skill contradicts the new default**

```bash
grep -rn "asOf" artmind/skills/*/SKILL.md
```

Expected: `artmind-refine/SKILL.md` (which defers to artmind-query's guidance — consistent, no change needed) and `artmind-update/SKILL.md` (describes the mechanism — no change needed). If any hit *instructs omitting* `--asOf` for present-tense questions, align it with the new default.

- [ ] **Step 3: Run the suite (guards against accidental code edits)**

```bash
uv run --group dev pytest test/ -v
```

Expected: ALL PASS.

- [ ] **Step 4: Commit**

```bash
git add artmind/skills/artmind-query/SKILL.md
git commit -m "docs(skills): default query skill to --asOf today current view

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: Refresh docs/INCREMENTAL_INGESTION.md and hand-off notes

The review document must reflect what this plan (and the already-landed §6.2 fix) changed, so it stays the authoritative write-up.

**Files:**
- Modify: `docs/INCREMENTAL_INGESTION.md`

- [ ] **Step 1: Update §6.1** — append to the end of the "No declaration → no linkage" paragraph:

```
> **Mitigated:** domains can now opt in via `temporal.defaults.supersede_on_title_family: true`
> in their schema — `detect_supersession` then infers a version chain among same-title-family
> documents ordered by `valid_from` (ties skipped, explicit notices take precedence,
> `detected_by: 'title_family'` on the edge). Off by default because dated *series*
> (meeting notes) share a family without superseding each other.
```

- [ ] **Step 2: Update §6.2** — prefix the section body with:

```
> **Fixed:** the hook now matches by `(name, entity_class, domain)` — the same key
> `_upsert_entity` merges on — and counts only entities the MATCH actually found.
> Entity-level canonical dates land at commit time; the refine `time` step remains
> a bulk backfill, not a prerequisite. The original defect is kept below for context.
```

- [ ] **Step 3: Update §6.3** — append to the end of the section:

```
> **Mitigated for supersessions:** when a commit applies a SUPERSEDES edge for the
> document, `_reassert_superseding_properties` overwrites the merged entities' domain
> properties with the superseding document's own staged values — updated scalars win,
> and date strings stay clean instead of accreting `"old | new"`. Accretion still
> applies between non-superseding peer documents (by design).
```

- [ ] **Step 4: Update §6.4** — append:

```
> **Mitigated at the skill layer:** the artmind-query skill now instructs `--asOf today`
> as the default retrieval posture, omitted only for explicitly historical questions.
> The CLI itself still applies no filter without `--asOf`.
```

- [ ] **Step 5: Update §8** — replace the four-item list's status by rewriting it as:

```
1. ~~Fix §6.2~~ — **done**: hook matches by `(name, entity_class, domain)`.
2. ~~Auto-propose supersession on title-family match~~ — **done**: schema-gated
   `supersede_on_title_family` inference in `detect_supersession`.
3. ~~Version-aware property merge~~ — **done** for the supersession case:
   `_reassert_superseding_properties` at commit. Per-property provenance remains
   future work if peer-document merges ever need it.
4. ~~Default current view~~ — **done at the skill layer**; a CLI/env-level default
   was deliberately not added (history queries must stay one flag away).
```

- [ ] **Step 6: Run the suite one final time and commit**

```bash
uv run --group dev pytest test/ -v
```

Expected: ALL PASS.

```bash
git add docs/INCREMENTAL_INGESTION.md
git commit -m "docs: update incremental-ingestion review for supersession hardening

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

- [ ] **Step 7: Report deployment caveats to the operator** (message, not code). Include these two items verbatim in your final summary:

1. Run-folder schemas are seeded **only when absent** — the packaged
   `banking.policy_schema.yaml` change does NOT reach an existing
   `~/.artmind/domains/schemas/banking.policy_schema.yaml`. The operator must add
   `supersede_on_title_family: true` to that copy by hand for live domains (and to
   any other domain that wants inference).
2. Skill edits reach the chat UI only via `artmind init` (see CLAUDE.md
   "Testing implications" §2) — the operator should run `artmind init` (or
   `just dev-install`), and restart any running `serve` daemon so the code changes
   are live (`just dev-stop-daemons`).

---

## Verification checklist (end of plan)

- `uv run --group dev pytest test/ -v` — full suite green.
- `just dev-cli-help` still shows `detect-supersession` under `ingest` with the new help text.
- Optional end-to-end (needs live Neo4j): re-ingest an edited copy of a `banking.policy` document *without* a supersession notice via `ARTMIND_NO_PROXY=1 artmind ingest sync <file> --domain banking.policy`, then confirm `artmind query graph timeline`/`metadata` shows the older version carrying `valid_to` and `superseded_by`, and a domain-property query with `--asOf today` returns the new value only.
