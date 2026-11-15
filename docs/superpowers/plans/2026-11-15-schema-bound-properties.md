# Schema-bound properties Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the properties step extract only schema-declared keys, show declared property values on brief rows, add a per-domain property audit, and add a targeted property backfill. Spec: `docs/superpowers/specs/2026-11-15-schema-bound-properties-design.md`.

**Architecture:** One helper, `observations.declared_property_keys`, gives each class's declared keys. Four readers use it: the extraction build path (`ingest._build_observations`), `retime.retime_observations` (prunes existing staging), the brief row builders (`brief.entity_line`), and the audit. The properties prompt renders blocks only for classes present in the chunk that declare properties. The backfill (`artmind/backfill.py`, new) re-asks the properties prompt for chosen classes and keys over staged chunk text, merges the answers into staged observations, and commits the way `retime` does. `retime --redoProperties` becomes a call into the backfill.

**Tech Stack:** Python 3.14, Click, pytest + `CliRunner`, Neo4j (only for real-run verification), `uv`.

**Companion:** `docs/superpowers/specs/2026-11-15-temporal-model-design.md` owns `temporal:` keys, `parse_valid` and record time. Both specs change `retime.retime_observations` (this plan adds the key filter; the temporal plan adds `parse_valid`) and `schema_validate`. Whichever lands second rebases onto the other. The temporal plan reuses the audit (Tasks 8–9) and the backfill (Tasks 10–11).

---

## Questions for review

**Status:** answered by the user, except 2 and 13 (open, awaiting explanation); 11 was checked (`plan.yaml` uses `domains`, a list of strings).

Each item is something in the spec that is wrong against the code, unclear, or that I would change. The plan follows my proposal; if you decide otherwise, change the task named.

1. **`_strip_exclusive` becomes a no-op (spec §2.2 is wrong about it).** `_exclusive_properties` maps an exclusive key to *every* class that declares it, and `_strip_exclusive` keeps the key for any class in that set. Under the class filter a class keeps a key only if it declares it, so `_strip_exclusive` removes nothing more. **Proposal (Task 4):** delete both functions. Keep `exclusive:` as a valid flag; the temporal spec may give it meaning. The existing exclusive tests still pass, because the filter does the stripping.

2. **The prompt still lists every class's block (§2.1).** `assemble_properties_prompt` renders all schema classes on every chunk. banking declares 447 keys, so most of each call is blocks for classes the chunk does not contain. Dropping only the vocabulary and the "NO fixed schema" text may not reach §8's 50% fall. **Proposal (Task 3):** `build_properties_prompt` renders only the classes of the entities it is given, and the DATES section only for those classes.

3. **A vault's `meta.yaml` is the user's copy (§2.1, §7).** `setup` and `init` seed `<vault>/.artmind/domains/meta.yaml` only when absent. All six vaults still carry the old "NO fixed schema" text, and the new template reaches a vault only through `artmind init --force`, which the spec names only for schemas. The old template also still contains `{{PROPERTY_VOCABULARY}}`, so the code must keep blanking that token. **Proposal:** keep the blanking (Task 3); add a `domains validate` WARN for the old template (Task 2); add `init --force` to the per-vault rollout.

4. **New cross-file checks must not block `setup` (§2.6).** `setup` calls `schema_validate.validate_all_or_raise`, so a new ERROR in `validate_schema` would stop `artmind setup` in any vault whose mart or view names an undeclared key. **Proposal (Task 2):** put mart, insights and view checks in `schema_validate.property_reference_report`, called only by `domains validate` (as `mart_validate` is). Only the `required:` and `keep_reason:` type checks go into `validate_schema`.

5. **View checks are heuristic, so they should warn (§2.6).** Views scope by domain, and a variable's class is known only from `(v:CLASS)`, `v.entity_class = 'X'` or `IN [...]`. **Proposal (Task 2):** WARN, skip unbound variables, promote to ERROR after a clean run on real vaults. **Evidence:** brain01's view `meetings_in_range` reads `e.location` on EVENT/ACTIVITY, but the journal schema declares `location` only on LOCATION. About 1,700 staged EVENT/ACTIVITY observations carry it, and §2.4 would drop them all. That schema needs `location` on EVENT and ACTIVITY before its retime.

6. **The shipped-guidance list (§2.5) is incomplete and partly wrong.** A scan of every shipped schema for `properties.<key>` and `RIGHT:` example keys finds:
   - `contracts` `guidance.entities`: `reference_as_written` (the spec lists this one);
   - `contracts` CHARGE guidance: "capture its other columns using the table's own column names in snake_case". The spec lists "the row's figures" but misses this half, which also asks for undeclared keys;
   - `banking.reference` SEVERITY_LEVEL: the `RIGHT:` example uses `impact_threshold`, but the declared keys are `financial_impact_threshold`, `customer_scope_threshold` and `system_downtime_threshold`;
   - the spec's "`banking.reference`: monthly values" (RATE_ENTRY) is **not** a defect: `rate_value` and `effective_date` are declared;
   - `guidance.relationships` (`properties.task`) concerns relationship properties, which the filter does not touch, so the scan skips it.

   **Proposal:** Task 1 fixes the three real defects; Task 2's scan checks `properties.<key>` and `RIGHT:` keys.

7. **An alias whose target no class declares.** The journal schema has `time_of_visit: time_of_day`, and no class declares `time_of_day`. Under the filter every value it renames is dropped. **Proposal:** WARN in `domains validate` (Task 2); remove the alias from the shipped journal schema and its skills copy (Task 1).

8. **Tables and `artmind update` (§2.2, non-goals).** A table2graph observation (`source_kind: table`) may carry mapping properties the schema does not declare; table2graph is out of scope, so retime leaves those alone. `update.write_user_chat` calls `build_observation` directly, not `_build_observations`, so the spec's filter misses chat updates. **Proposal (Task 4):** apply the filter in `write_user_chat` too.

9. **The brief budget across many rows (§5).** 600 property characters per row, times `pattern1`'s default 200 rows, is about 120,000 characters, far over the 24,000-character agent budget. Also only `entity-context` rows carry `truncated_properties` today. **Proposal (Task 7):** 600 characters per row for `entity-context` and `pattern2`; 200 for the listing patterns `pattern1`, `pattern7` and `pattern8`. Both are module constants.

10. **Audit rule order (§3).** As written, a key the operator marked `required: true`, with low fill and a high `inferred` share, hits rule 4 and gets `drop`. That is the `trigger`/`resolution` case the spec wants kept. **Proposal (Task 8):** `required: true` reports `required`; `keep_reason`, `temporal` and `depended_on` give `keep`, before the fill and overlap rules.

11. **"`depended_on: skill`" is undefined (§3).** **Proposal (Task 8):** a key counts as skill-referenced when `` `key` `` or `` `CLASS.key` `` appears in a saved view's `SKILL.md`, or in a topic plan's `SKILL.md` that covers the domain. Drop this rule if you meant something else.

12. **The backfill's effect block (§4).** `reversible: true` needs an `undo` the user can run. **Proposal:** the stash keeps each changed folder's `observations.json` under `logs/backfill-stash/<run_id>/`; `undo` names it and the restore steps (copy back, then `artmind ingest retime --domain d`). The spec's test file name breaks the repo's naming rule; it must be `test/test_dry_run_ingest_backfill_properties.py`. Add `property_values_dropped` and `property_backfill` to `cli_conventions.EFFECT_KINDS`, rather than the spec's generic `observations`.

13. **`--redoProperties` "with the same output" (§4).** Today the redo is one call per chunk covering every dated class. A one-class backfill per dated class would make up to three calls per journal chunk. **Proposal (Tasks 10–11):** the library takes `targets: {class: [keys]}`; retime passes all dated classes in one pass; it reads staged `chunks.json`, so the chunk-cache requirement goes. Two behaviour differences: a key the model does not answer is now kept (today the whole property set is replaced), and the old helpers are deleted.

14. **The backfill should also patch the chunk cache.** Otherwise a rebuild of `observations.json` from `chunks/<sha>/chunk_NNN.json` would lose the backfilled values. **Proposal (Task 10):** best-effort write into the chunk file's `properties`, matched by raw `name` and `entity_class`.

15. **A `skipped` chunk is re-asked after a schema change (§2.3).** Resume re-runs any properties step that is not `ok`, so a chunk skipped because its classes declared nothing is asked again once a property is declared. That fills the new key, which is right, but it is an LLM call nobody asked for. Accept it, or record such chunks `ok`.

16. **Retime and missing markdown (§2.4).** `retime_folder` returns `skipped` without the markdown, leaving undeclared keys in place. **Proposal (Task 6):** still apply the key filter and keep the staged dates; the row says `"dates": "kept (markdown not found)"`.

---

## File structure

| File | Responsibility | Tasks |
|---|---|---|
| `artmind/observations.py` | `declared_property_keys`, `declared_property_order`, `filter_declared`, `OBSERVATION_OWN_KEYS` | 4 |
| `artmind/domains/meta.yaml` | `prompt_templates.properties` text | 3 |
| `artmind/prompt_builder.py` | `_render_property_block`; class-scoped `assemble_properties_prompt`; vocabulary section removed | 3 |
| `artmind/extraction.py` | `build_properties_prompt` scopes to the entities' classes; `vocabulary` parameter removed | 3 |
| `artmind/canonicalize.py` | `retrieve_property_vocabulary`, `render_property_vocabulary` removed | 3 |
| `artmind/ingest.py` | `_build_observations` filter and drop count; no call for property-less classes; vocabulary call removed; `_strip_exclusive` removed; redo-only parameters removed | 3, 4, 5, 11 |
| `artmind/retime.py` | key filter on staged observations; dropped-values sidecar; `property_values_dropped` change; `--redoProperties` delegates to the backfill | 4, 6, 11 |
| `artmind/update.py` | filter in `write_user_chat` | 4 |
| `artmind/schema_validate.py` | `required`/`keep_reason` types; alias, guidance and meta-template WARNs; `property_reference_report` (mart, insights, views) | 2, 8 |
| `artmind/cli.py` | `domains validate` wiring; `domains property-audit`; `ingest backfill-properties`; `COMMAND_GROUPS` | 2, 9, 10 |
| `artmind/cli_conventions.py` | `EFFECT_KINDS` += `property_values_dropped`, `property_backfill`; `WRITE_COMMANDS` += `ingest backfill-properties` | 6, 10 |
| `artmind/brief.py` | `entity_line(entity, declared=None, budget=...)`; `pattern_row` / `entity_context_row` take the declared order | 7 |
| `artmind/graph_query.py` | `_declared_orders(domains)` for brief rows | 7 |
| `artmind/property_audit.py` (new) | staging scan, overlap shares, flags, proposal rules, depended-on and conflict reads | 8 |
| `artmind/backfill.py` (new) | targets, chunk selection, prompt, cache, merge, stash, effect, commit | 10 |
| `artmind/domains/schemas/{contracts,banking.reference,personal_journal}_schema.yaml`; `artmind/skills/artmind-create-schema/assets/personal_journal_schema.yaml` | §2.5 guidance fixes; alias fix | 1 |
| Skills, `justfile`, `CLAUDE.md` | docs for each new surface | 12 |

## How to verify beyond the hermetic suite

- **Run this worktree's code, not the global command.** The global `artmind` is an editable install of the *main* checkout. Set `WT` and run `uv run --project $WT artmind …` from inside a vault:
  - `export WT=/Users/surjitdas/Projects/artmind9/.claude/worktrees/plan-schema-bound-properties-8cf5fb`
- **Always set `ARTMIND_NO_PROXY=1`.** A running `serve` would answer `query` calls with old code.
- **Prefix any LLM-calling command with `env -u ANTHROPIC_BASE_URL`** (CLAUDE.md, Testing implications §5).
- **Keep brain01 output aggregate.** Use banking_corpus (public) for anything that prints values. Use brain01 only for counts, key names and class names. Never put brain01 values, names or text into commits, tests or messages.
- **Never run `artmind setup` or `artmind init` from the checkout.**
- **Ask before any real write to a vault.** `--dryRun` is always safe. A non-dry `retime` or `backfill-properties` rewrites committed staging and the graph.

---

## Part 1 — Schema guidance fixes and validation (spec §2.5, §2.6)

### Task 1: Fix shipped schema guidance and the journal alias

**Files:**
- Modify: `artmind/domains/schemas/contracts_schema.yaml` — `guidance.entities` (lines ~50–58), CHARGE `guidance` (lines ~388–396), and the AMENDMENT, CHARGE and TERM `properties`.
- Modify: `artmind/domains/schemas/banking.reference_schema.yaml` — SEVERITY_LEVEL guidance example (lines ~222–228).
- Modify: `artmind/domains/schemas/personal_journal_schema.yaml` (lines ~28–34) and `artmind/skills/artmind-create-schema/assets/personal_journal_schema.yaml` — the `time_of_visit` alias.
- Test: `test/test_shipped_schema_property_refs.py` (new).

- [ ] **Step 1: Write the failing test.** The scan is inline so this task lands alone; Task 2 replaces it with `schema_validate.guidance_property_warnings`.

```python
"""Shipped schemas point the extractor only at keys their classes declare (spec 2026-11-15 §2.5)."""
import re
from pathlib import Path

import pytest
import yaml

SCHEMAS = Path(__file__).resolve().parent.parent / "artmind" / "domains" / "schemas"
COMMON = {"name", "aliases", "entity_class", "type", "description", "context"}
_DOTTED = re.compile(r"\bproperties\.([a-z][a-z0-9_]*)")
_EXAMPLE_KEY = re.compile(r'"([a-z][a-z0-9_]*)"\s*:')


def _undeclared(schema: dict) -> list[str]:
    types = schema.get("entity_types") or {}
    declared = {c: set((d or {}).get("properties") or {}) for c, d in types.items()}
    every = set().union(*declared.values()) if declared else set()
    found = []
    g = schema.get("guidance") or {}
    for part in ("entities", "properties"):
        for key in _DOTTED.findall(str(g.get(part) or "")):
            if key not in every and key not in COMMON:
                found.append(f"guidance.{part}: {key}")
    for cls, decl in types.items():
        text = f"{(decl or {}).get('description') or ''}\n{(decl or {}).get('guidance') or ''}"
        keys = set(_DOTTED.findall(text))
        keys |= {k for line in text.splitlines() if "RIGHT:" in line for k in _EXAMPLE_KEY.findall(line)}
        found += [f"{cls}: {k}" for k in sorted(keys) if k not in declared[cls] and k not in COMMON]
    return found


@pytest.mark.parametrize("path", sorted(SCHEMAS.glob("*_schema.yaml")), ids=lambda p: p.stem)
def test_guidance_names_only_declared_keys(path):
    assert _undeclared(yaml.safe_load(path.read_text(encoding="utf-8"))) == []


@pytest.mark.parametrize("path", sorted(SCHEMAS.glob("*_schema.yaml")), ids=lambda p: p.stem)
def test_every_alias_targets_a_declared_key(path):
    schema = yaml.safe_load(path.read_text(encoding="utf-8"))
    declared = {p for d in (schema.get("entity_types") or {}).values() for p in ((d or {}).get("properties") or {})}
    assert {a: t for a, t in (schema.get("property_aliases") or {}).items() if t not in declared} == {}


def test_charge_guidance_does_not_invite_column_named_keys():
    schema = yaml.safe_load((SCHEMAS / "contracts_schema.yaml").read_text(encoding="utf-8"))
    assert "table's own column names" not in str(schema["entity_types"]["CHARGE"]["guidance"])
```

- [ ] **Step 2: Run it and confirm it fails.**

Run: `uv run --group dev pytest test/test_shipped_schema_property_refs.py -v`
Expected: failures for `contracts` (`reference_as_written`), `banking.reference` (`impact_threshold`), `personal_journal` (the alias), and the CHARGE column-names test.

- [ ] **Step 3: Fix the schemas.**

In `contracts_schema.yaml`:

1. Add to the `properties` of AMENDMENT, CHARGE and TERM:

```yaml
      reference_as_written:
        hint: a change-note number the text attributes this to when it differs from the document's own reference, exactly as written
```

2. In the CHARGE `guidance`, replace the sentences from "Put the row's figures in properties, and capture its other columns…" to the end with:

```yaml
      location) the row describes, never after the numeric value. Put the row's figures in its declared
      properties: amount, unit, role, location, forecast_volume_days, forecast_spend. A column with no
      declared property goes in the description, never under a new key.
```

3. In `guidance.entities`, change "put the row's figures in properties." to "put the row's figures in the CHARGE's declared properties."

In `banking.reference_schema.yaml`, replace the SEVERITY_LEVEL `RIGHT:` line with:

```yaml
        RIGHT:   { "name": "Severity 1 (Critical)", "financial_impact_threshold": ">£100,000", "response_time_target": "4 hours" }
```

In `personal_journal_schema.yaml` and its skills-assets copy: delete `  time_of_visit: time_of_day`, and add above `property_aliases:` the comment `# Time of day belongs in date_or_time text (e.g. "2026-01-07 (evening)"), not in a key of its own.`

- [ ] **Step 4: Run it and confirm it passes, then run the schema-reading suites.**

Run: `uv run --group dev pytest test/test_shipped_schema_property_refs.py test/test_schema_validate.py test/test_schema_naming_guidance.py test/test_journal_schema_guidance.py test/test_property_keys.py -v`
Expected: PASS. If `test_journal_schema_guidance.py` asserts the alias, update it to assert the comment.

- [ ] **Step 5: Commit.**

```bash
git add artmind/domains/schemas artmind/skills/artmind-create-schema/assets/personal_journal_schema.yaml test/test_shipped_schema_property_refs.py
git commit -m "fix(schemas): guidance names only declared property keys"
```

**Beyond the suite:** `cd ~/artmind_vaults/banking_corpus && ARTMIND_NO_PROXY=1 uv run --project $WT artmind domains properties-prompt banking.reference --compact` should show the new SEVERITY_LEVEL example. Vaults keep their own schema copies, so the fix reaches a vault only through `artmind init --force --schema contracts` (or `banking.reference`), run inside that vault during rollout.

---

### Task 2: Property checks in `domains validate`

**Files:**
- Modify: `artmind/schema_validate.py` — `validate_schema` gains `_property_decl_errors`; new `property_alias_warnings`, `guidance_property_warnings`, `meta_template_warnings`, `view_property_refs`, `property_reference_report`; `warn_all` extended.
- Modify: `artmind/cli.py` lines ~738–808 (`domains_validate`).
- Modify: `test/test_shipped_schema_property_refs.py` — use the new functions instead of the inline helper.
- Test: `test/test_schema_validate_properties.py` (new).

- [ ] **Step 1: Write the failing tests.**

```python
"""`domains validate` property checks (spec 2026-11-15 §2.5, §2.6). Synthetic schemas only."""
from types import SimpleNamespace

import yaml

from artmind import schema_validate as sv

META = {"reserved_prefix": "_", "kinds": {"recurrent": "", "occurrent": ""}}


def _schema(**props):
    return {"name": "d", "entity_types": {"EVENT": {"kind": "occurrent", "properties": props}}}


def _no_mart(monkeypatch):
    monkeypatch.setattr("artmind.mart.declaration.load_declaration", lambda d: None)


def _mart(person_seed=None, type_property="type", wholes=None):
    return SimpleNamespace(person=SimpleNamespace(entity_class="PERSON", seed_property=person_seed),
                           place=SimpleNamespace(entity_class="LOCATION", seed_property=None),
                           type=SimpleNamespace(classes=["EVENT"], property=type_property),
                           happening_classes=["EVENT"], wholes=wholes or {})


def test_required_must_be_a_boolean():
    errors = sv.validate_schema(_schema(outcome={"required": "yes"}), META)
    assert any("EVENT.properties.outcome.required" in e and "true or false" in e for e in errors)


def test_keep_reason_must_be_a_string():
    assert any("keep_reason" in e for e in sv.validate_schema(_schema(outcome={"keep_reason": 3}), META))


def test_a_valid_required_and_keep_reason_pass():
    assert sv.validate_schema(_schema(outcome={"required": True, "keep_reason": "probe M04"}), META) == []


def test_an_alias_to_an_undeclared_key_warns():
    schema = {**_schema(outcome={}), "property_aliases": {"result": "outcome", "time_of_visit": "time_of_day"}}
    (w,) = sv.property_alias_warnings(schema, "d")
    assert "time_of_visit" in w and "time_of_day" in w and "dropped" in w


def test_guidance_naming_an_undeclared_key_warns():
    schema = _schema(outcome={})
    schema["entity_types"]["EVENT"]["guidance"] = 'Record properties.cause. RIGHT: { "name": "X", "mood": "calm" }'
    warnings = sv.guidance_property_warnings(schema, "d")
    assert any("EVENT" in w and "cause" in w for w in warnings)
    assert any("EVENT" in w and "mood" in w for w in warnings)


def test_relationship_guidance_is_not_scanned():
    schema = {**_schema(outcome={}), "guidance": {"relationships": "always include properties.task"}}
    assert sv.guidance_property_warnings(schema, "d") == []


def test_the_old_meta_template_warns(tmp_path):
    meta = tmp_path / "meta.yaml"
    meta.write_text(yaml.safe_dump({"prompt_templates": {"properties": "There is NO fixed schema for properties"}}))
    (w,) = sv.meta_template_warnings(meta)
    assert "init --force" in w


def test_a_mart_seed_on_an_undeclared_key_is_an_error(monkeypatch):
    monkeypatch.setattr("artmind.mart.declaration.load_declaration", lambda d: _mart(person_seed="relation"))
    schema = {"entity_types": {"PERSON": {"properties": {"role": {}}}, "LOCATION": {}, "EVENT": {}}}
    assert any("mart" in e and "PERSON.relation" in e for e in sv.property_reference_report("d", schema)["errors"])


def test_type_is_structural_and_always_declared(monkeypatch):
    monkeypatch.setattr("artmind.mart.declaration.load_declaration", lambda d: _mart())
    schema = {"entity_types": {"PERSON": {}, "LOCATION": {}, "EVENT": {}}}
    assert sv.property_reference_report("d", schema)["errors"] == []


def test_a_mart_whole_match_key_must_be_declared_on_a_happening_class(monkeypatch):
    wholes = {"life_event": SimpleNamespace(match=["edge", "name", "participants"])}
    monkeypatch.setattr("artmind.mart.declaration.load_declaration", lambda d: _mart(wholes=wholes))
    schema = {"entity_types": {"PERSON": {}, "LOCATION": {}, "EVENT": {"properties": {"outcome": {}}}}}
    assert any("participants" in e for e in sv.property_reference_report("d", schema)["errors"])


def test_insights_open_loops_on_an_undeclared_key_is_an_error(monkeypatch):
    _no_mart(monkeypatch)
    cfg = SimpleNamespace(block=lambda n: {"class": "PLAN", "property": "state", "urgency_property": "urgency"}
                          if n == "open_loops" else None)
    monkeypatch.setattr("artmind.insights.config.load_config", lambda d: cfg)
    schema = {"entity_types": {"PLAN": {"properties": {"status": {}, "urgency": {}}}}}
    assert any("insights" in e and "PLAN.state" in e for e in sv.property_reference_report("d", schema)["errors"])


def test_a_view_reading_an_undeclared_key_on_a_bound_class_warns(monkeypatch):
    _no_mart(monkeypatch)
    view = SimpleNamespace(spec=SimpleNamespace(name="v1", domains=("d",), cypher=(
        "MATCH (e:Entity) WHERE e.entity_class IN ['EVENT'] RETURN e.location, e.date_or_time, e._id, e.name")))
    monkeypatch.setattr("artmind.kg_views.store.list_views", lambda domains=None: ([view], []))
    schema = {"entity_types": {"EVENT": {"properties": {"date_or_time": {}}}}}
    report = sv.property_reference_report("d", schema)
    assert report["errors"] == []
    assert any("view:v1" in w and "EVENT.location" in w for w in report["warnings"])
```

- [ ] **Step 2: Run them and confirm they fail.**

Run: `uv run --group dev pytest test/test_schema_validate_properties.py -v`
Expected: `AttributeError: module 'artmind.schema_validate' has no attribute 'property_alias_warnings'` and similar.

- [ ] **Step 3: Implement.** In `artmind/schema_validate.py`, add:

```python
#: Keys every entity carries whatever its class declares (meta.yaml `entity_common_properties`).
COMMON_KEYS = frozenset({"name", "aliases", "entity_class", "type", "description", "context"})
#: Graph-side keys a view may read on any entity.
_NODE_KEYS = frozenset({"key", "id", "entity_class", "embedding", "label"})
_DOTTED_KEY = re.compile(r"\bproperties\.([a-z][a-z0-9_]*)")
_EXAMPLE_KEY = re.compile(r'"([a-z][a-z0-9_]*)"\s*:')
_FIELD_REF = re.compile(r"\b([a-z][A-Za-z0-9_]*)\.([a-z][a-z0-9_]*)\b")
_LABEL_BIND = re.compile(r"\(\s*([a-z][A-Za-z0-9_]*)\s*:\s*([A-Z][A-Z0-9_]*)\b")
_CLASS_EQ = re.compile(r"\b([a-z][A-Za-z0-9_]*)\.entity_class\s*(?:=\s*'([A-Z0-9_]+)'|IN\s*\[([^\]]*)\])")


def _declared(entity_types: dict) -> dict[str, set[str]]:
    return {c: set((d or {}).get("properties") or {}) for c, d in entity_types.items() if isinstance(d, dict)}


def _property_decl_errors(entity_types: dict, label: str) -> list[str]:
    errors = []
    for cls, decl in entity_types.items():
        if not isinstance(decl, dict):
            continue
        for prop, pd in (decl.get("properties") or {}).items():
            if not isinstance(pd, dict):
                continue
            where = f"{label}.entity_types.{cls}.properties.{prop}"
            if "required" in pd and not isinstance(pd["required"], bool):
                errors.append(f"{where}.required: must be true or false, not {pd['required']!r}")
            if "keep_reason" in pd and not (isinstance(pd["keep_reason"], str) and pd["keep_reason"].strip()):
                errors.append(f"{where}.keep_reason: must be a non-empty string")
    return errors


def property_alias_warnings(schema: dict, schema_name: str = "") -> list[str]:
    """An alias whose target no class declares: under schema-only keys its values are dropped."""
    label = schema_name or schema.get("name", "<unnamed>")
    declared = _declared(schema.get("entity_types") or {})
    every = set().union(*declared.values()) if declared else set()
    return [
        f"{label}.property_aliases: {a!r} -> {t!r}, but no class declares {t!r}, so every value it renames is dropped"
        for a, t in (schema.get("property_aliases") or {}).items() if str(t) not in every
    ]


def guidance_property_warnings(schema: dict, schema_name: str = "") -> list[str]:
    """Guidance or a `RIGHT:` example naming a key its class does not declare (spec §2.5).
    Domain `guidance.entities`/`.properties` is checked against every class; a class's own
    description/guidance against that class. Relationship guidance is not scanned."""
    label = schema_name or schema.get("name", "<unnamed>")
    types = schema.get("entity_types") or {}
    declared = _declared(types)
    every = set().union(*declared.values()) if declared else set()
    out = []
    for part in ("entities", "properties"):
        for key in sorted(set(_DOTTED_KEY.findall(str((schema.get("guidance") or {}).get(part) or "")))):
            if key not in every and key not in COMMON_KEYS:
                out.append(f"{label}.guidance.{part}: names properties.{key}, which no class declares")
    for cls, decl in types.items():
        if not isinstance(decl, dict):
            continue
        text = f"{decl.get('description') or ''}\n{decl.get('guidance') or ''}"
        keys = set(_DOTTED_KEY.findall(text))
        keys |= {k for line in text.splitlines() if "RIGHT:" in line for k in _EXAMPLE_KEY.findall(line)}
        for key in sorted(keys - declared.get(cls, set()) - COMMON_KEYS):
            out.append(f"{label}.entity_types.{cls}: guidance names {key!r}, which {cls} does not declare")
    return out


def meta_template_warnings(meta_path: Path = DOMAIN_META_PATH) -> list[str]:
    try:
        text = str((load_meta(meta_path).get("prompt_templates") or {}).get("properties") or "")
    except Exception:
        return []
    if "NO fixed schema" in text:
        return [f"{meta_path}: the properties prompt still invites keys the schema does not declare; "
                "run `artmind init --force` inside the vault to take the packaged meta.yaml"]
    return []


def view_property_refs(cypher: str, declared: dict[str, set[str]]) -> list[tuple[frozenset, str]]:
    """(classes a variable is bound to, key) for every `var.key` a view reads on a class-bound variable."""
    from artmind.kg_views.model import _mask

    text = _mask(cypher)  # literals and comments blanked: 'a.b' inside a string is not a reference
    bound: dict[str, set[str]] = {}
    for var, cls in _LABEL_BIND.findall(text):
        if cls in declared:
            bound.setdefault(var, set()).add(cls)
    for var, one, many in _CLASS_EQ.findall(cypher):
        names = {one} if one else set(re.findall(r"'([A-Z0-9_]+)'", many))
        bound.setdefault(var, set()).update(n for n in names if n in declared)
    refs = []
    for var, key in sorted(set(_FIELD_REF.findall(text))):
        classes = bound.get(var)
        if classes and key not in COMMON_KEYS and key not in _NODE_KEYS and not key.startswith("_"):
            refs.append((frozenset(classes), key))
    return refs


def property_reference_report(domain: str, schema: dict) -> dict:
    """Keys the mart declaration, insights config or a saved view reads that the schema does not
    declare (spec §2.6). Only `domains validate` calls this, never `setup`. {"errors", "warnings"}."""
    declared = _declared(schema.get("entity_types") or {})
    errors, warnings = [], []

    def has(cls, key):
        return key in COMMON_KEYS or key.startswith("_") or key in declared.get(cls, set())

    from artmind.mart.declaration import load_declaration
    try:
        decl = load_declaration(domain)
    except Exception:
        decl = None  # mart_validate already reports a broken declaration
    if decl is not None:
        for label, cls, key in (("dimensions.person.seed", decl.person.entity_class, decl.person.seed_property),
                                ("dimensions.place.seed", decl.place.entity_class, decl.place.seed_property)):
            if key and not has(cls, key):
                errors.append(f"mart/{domain} {label}: {cls}.{key} is not declared")
        for cls in decl.type.classes:
            if not has(cls, decl.type.property):
                errors.append(f"mart/{domain} dimensions.type.property: {cls}.{decl.type.property} is not declared")
        for kind, w in (decl.wholes or {}).items():
            for key in [m for m in (getattr(w, "match", None) or []) if m not in ("edge", "name")]:
                if not any(has(c, key) for c in decl.happening_classes):
                    errors.append(f"mart/{domain} wholes.{kind}.match: {key!r} is declared on none of "
                                  f"{', '.join(decl.happening_classes)}")

    from artmind.insights.config import InsightsError, load_config
    try:
        cfg = load_config(domain)
    except InsightsError:
        cfg = None  # `insights config check` reports it
    if cfg is not None:
        outcome, loops = cfg.block("outcome"), cfg.block("open_loops")
        if outcome and not has(outcome["class"], outcome["property"]):
            errors.append(f"insights/{domain} outcome.property: {outcome['class']}.{outcome['property']} is not declared")
        if loops:
            for field in ("property", "urgency_property"):
                if not has(loops["class"], loops[field]):
                    errors.append(f"insights/{domain} open_loops.{field}: {loops['class']}.{loops[field]} is not declared")

    from artmind.kg_views.store import list_views
    views, _invalid = list_views([domain])
    for view in views:
        for classes, key in view_property_refs(view.spec.cypher, declared):
            if not any(key in declared[c] for c in classes):
                warnings.append(f"view:{view.spec.name} reads {'/'.join(sorted(classes))}.{key}, "
                                "which no class it scopes to declares")
    return {"errors": errors, "warnings": warnings}
```

Then:
- In `validate_schema`, add `errors.extend(_property_decl_errors(entity_types, label))` just before `return errors`.
- In `warn_all`, add `property_alias_warnings(schema, name) + guidance_property_warnings(schema, name)` to `warnings`.

In `cli.py` `domains_validate`:
1. Extend the single-`domain` branch's `warnings` with the same two functions.
2. After the mart block, add:

```python
    from artmind.schema_validate import meta_template_warnings, property_reference_report
    from artmind.temporal import load_schema

    names = [domain] if domain else [p.name.removesuffix("_schema.yaml")
                                     for p in sorted(DOMAIN_SCHEMAS_DIR.glob("*_schema.yaml"))]
    refs = {}
    for name in names:
        rep = property_reference_report(name, load_schema(name, schemas_dir=DOMAIN_SCHEMAS_DIR))
        if rep["errors"] or rep["warnings"]:
            refs[name] = rep
    for w in meta_template_warnings():
        click.echo(f"WARN   {w}", err=True)
    for rep in refs.values():
        for w in rep["warnings"]:
            click.echo(f"WARN   {w}", err=True)
    refs_failed = {n: r for n, r in refs.items() if r["errors"]}
```

3. Add `"property_references": refs` to `result`; `ok` also requires `not refs_failed`.
4. Print `ERROR  {e}` for each error, and count `refs_failed` in the final `ClickException`.

In `test/test_shipped_schema_property_refs.py`, replace `_undeclared(...)` with `sv.guidance_property_warnings(schema, path.stem) == []`, and the alias test with `sv.property_alias_warnings(schema, path.stem) == []`.

- [ ] **Step 4: Run them and confirm they pass.**

Run: `uv run --group dev pytest test/test_schema_validate_properties.py test/test_shipped_schema_property_refs.py test/test_schema_validate.py test/test_mart_validate_cli.py test/test_cli_conventions.py -v`

- [ ] **Step 5: Commit.**

```bash
git add artmind/schema_validate.py artmind/cli.py test/test_schema_validate_properties.py test/test_shipped_schema_property_refs.py
git commit -m "feat(domains validate): property declaration, alias, guidance and reference checks"
```

**Beyond the suite** (read-only): `cd ~/artmind_vaults/brain01 && ARTMIND_NO_PROXY=1 uv run --project $WT artmind domains validate --compact 2>&1 >/dev/null | grep -E "WARN|ERROR"`. Expect the meta-template WARN, `view:meetings_in_range reads ACTIVITY/EVENT.location`, and the `time_of_visit` alias WARN. These name keys, not journal content, so they are safe to share.

---

## Part 2 — Prompt and build-time filter (spec §2.1–§2.4)

### Task 3: The properties prompt: schema-only, class-scoped, no vocabulary

**Files:**
- Modify: `artmind/domains/meta.yaml` lines ~135–185 (`prompt_templates.properties`).
- Modify: `artmind/prompt_builder.py` — `_render_property_block` (lines ~146–157); delete `_render_property_vocabulary_section` (lines ~160–175); `assemble_properties_prompt` (lines ~226–254).
- Modify: `artmind/extraction.py` lines ~383–406 (`build_properties_prompt`).
- Modify: `artmind/ingest.py` — delete the property-vocabulary block (lines ~3924–3946); the call at lines ~4149–4152.
- Modify: `artmind/canonicalize.py` — delete `PROPERTY_VOCABULARY_LIMIT` (line ~54), `retrieve_property_vocabulary` and `render_property_vocabulary` (lines ~192–280).
- Modify tests: `test/test_prompt_builder.py`, `test/test_canonicalize.py`, `test/test_property_keys.py`, `test/test_extraction.py`, `test/test_chunks_only_extract.py` (line ~28).

- [ ] **Step 1: Write the failing tests.** Append to `test/test_prompt_builder.py` (its `SCHEMA` has PERSON with `role`/`affiliation`, and LOCATION with no properties):

```python
def test_properties_prompt_is_schema_only():
    result = assemble_properties_prompt(SCHEMA)
    assert "Extract ONLY the properties listed" in result and "Never add a key" in result
    assert "NO fixed schema" not in result
    assert "For PERSON, extract:" in result and "consider:" not in result


def test_a_required_key_is_marked():
    schema = {"entity_types": {"PERSON": {"kind": "recurrent", "properties": {
        "role": {"hint": "their role", "required": True}, "affiliation": {}}}}}
    result = assemble_properties_prompt(schema)
    assert "- role (their role) (required when stated)" in result
    assert "- affiliation (required" not in result


def test_only_the_given_classes_are_rendered():
    schema = {"entity_types": {"PERSON": {"kind": "recurrent", "properties": {"role": {}}},
                               "ORG": {"kind": "recurrent", "properties": {"sector": {}}}}}
    result = assemble_properties_prompt(schema, classes={"ORG"})
    assert "For ORG, extract:" in result and "For PERSON" not in result


def test_an_old_template_token_is_blanked():
    meta = load_meta()
    old = {**meta, "prompt_templates": {**meta["prompt_templates"],
                                        "properties": "{{PROPERTY_BLOCKS}}\n{{PROPERTY_VOCABULARY}}\n{entities_list}{text}"}}
    assert "{{PROPERTY_VOCABULARY}}" not in assemble_properties_prompt(SCHEMA, meta=old)
```

Add to `test/test_extraction.py`:

```python
def test_build_properties_prompt_renders_only_the_entities_classes():
    schema = {"entity_types": {"PERSON": {"kind": "recurrent", "properties": {"role": {}}},
                               "ORG": {"kind": "recurrent", "properties": {"sector": {}}}}}
    prompt = build_properties_prompt("t", [{"id": "e1", "entity_class": "ORG", "name": "Acme"}], schema)
    assert "For ORG, extract:" in prompt and "For PERSON" not in prompt
```

- [ ] **Step 2: Run them and confirm they fail.**

Run: `uv run --group dev pytest test/test_prompt_builder.py test/test_extraction.py -v`
Expected: the new tests FAIL (template unchanged, no `classes` parameter, blocks still say "consider:").

- [ ] **Step 3: Implement.**

In `meta.yaml` `prompt_templates.properties`, replace the two "There is NO fixed schema…" lines and the `{{PROPERTY_VOCABULARY}}` line; drop key-naming rules 1 and 8 (they only tamed invented keys); renumber:

```yaml
    Extract ONLY the properties listed for each entity's class below, using exactly
    those key names. Omit a property the passage does not state. Never add a key
    that is not listed.

    {{PROPERTY_BLOCKS}}
    {{GUIDANCE}}
    KEY RULES FOR PROPERTIES:
      1. Use arrays for multiple values
      2. Omit a property entirely rather than writing null or "unknown"
      3. Values should be specific to THIS document, not generic definitions
      4. Keep individual values concise; a value is a plain string, number or list of them
      5. If a property feels forced or uncertain, leave it out
```

In the QUALITY CHECKLIST, change "No hallucinated properties?" to "Only listed keys, each stated in the passage?".

In `prompt_builder.py`:

```python
def _render_property_block(cls: str, decl: dict) -> str:
    properties = decl.get("properties") or {}
    if not properties:
        return ""
    lines = [f"For {cls}, extract:"]
    for name, prop_decl in properties.items():
        pd = prop_decl if isinstance(prop_decl, dict) else {}
        hint = pd.get("hint", "")
        lines.append(f"  - {name}" + (f" ({hint})" if hint else "")
                     + (" (required when stated)" if pd.get("required") is True else ""))
    guidance = (decl.get("guidance") or "").strip()
    if guidance:
        lines.append(f"  {guidance}")
    return "\n".join(lines)


def assemble_properties_prompt(
    schema: dict,
    meta: dict | None = None,
    anchor_date: str | None = None,
    classes: "set[str] | None" = None,
) -> str:
    """`classes` limits the class blocks and the DATES section to these classes -- the classes of
    the chunk's entities (`extraction.build_properties_prompt`); None renders every class (the
    `domains properties-prompt` command)."""
    meta = meta if meta is not None else load_meta()
    entity_types = {c: d for c, d in (schema.get("entity_types") or {}).items()
                    if classes is None or c in classes}
    blocks = [_render_property_block(cls, decl) for cls, decl in entity_types.items() if decl.get("properties")]
    dates = _render_anchor_date_section(entity_types, anchor_date)
    if dates:
        blocks.append(dates)
    tokens = {
        "SUBJECT": schema.get("subject") or schema.get("description", "")[:120],
        "PROPERTY_BLOCKS": "\n\n".join(blocks),
        # A vault's meta.yaml older than this change still carries the token: blank it.
        "PROPERTY_VOCABULARY": "",
        "GUIDANCE": _render_guidance_section(
            (schema.get("guidance") or {}).get("properties"), "DOMAIN-SPECIFIC GUIDANCE:"
        ),
    }
    return _fill(meta["prompt_templates"]["properties"], tokens)
```

Delete `_render_property_vocabulary_section`. Run `rg -n "assemble_properties_prompt\(" artmind test` and make sure any positional `anchor_date` call passes it by keyword.

In `extraction.py`:

```python
def build_properties_prompt(
    text: str,
    entities: list[dict],
    schema: dict,
    anchor_date: str | None = None,
    *,
    meta: dict | None = None,
) -> str:
    """Only the classes of `entities` are rendered (spec 2026-11-15 §2.1). `anchor_date`: the
    document's own date for relative phrases. `meta`: an alternative meta.yaml dict
    (`artmind review reextract`); None = the run folder's."""
    from artmind.prompt_builder import assemble_properties_prompt

    classes = {e.get("entity_class") for e in entities if e.get("entity_class")}
    return (
        assemble_properties_prompt(schema, meta=meta, anchor_date=anchor_date, classes=classes)
        .replace("{entities_list}", entities_list_text(entities))
        .replace("{text}", text)
    )
```

In `ingest.py`: delete the `property_vocabulary` block (lines ~3924–3946), and change the call to `build_properties_prompt(text, prompt_entities, schema, anchor_date=anchor_date)`. `reviews/reextract.py:53` and `update.py:83` already fit.

In `canonicalize.py`: delete the three names above.

Test edits:
- `test/test_canonicalize.py`: delete the imports at lines ~13–14 and the vocabulary tests at lines ~125–198.
- `test/test_property_keys.py`: delete `test_retrieved_vocabulary_drops_keys_that_would_be_renamed`.
- `test/test_prompt_builder.py`: delete `test_assemble_properties_prompt_renders_the_property_vocabulary_when_given` and `test_assemble_properties_prompt_instructs_singular_key_naming`; in `test_assemble_properties_prompt_lists_properties_and_hints`, change "consider:" to "extract:".
- `test/test_extraction.py:55`: drop `vocabulary=`.
- `test/test_chunks_only_extract.py:28`: delete the `retrieve_property_vocabulary` line (`monkeypatch.setattr` on a missing attribute raises).

Stubs whose lambdas keep `vocabulary=None` still work, because the call no longer passes it.

- [ ] **Step 4: Run them and confirm they pass, then the full suite** (prompt text reaches many tests).

Run: `uv run --group dev pytest test/test_prompt_builder.py test/test_extraction.py test/test_canonicalize.py test/test_property_keys.py test/test_chunks_only_extract.py -v && just dev-test`
Expected: PASS. A test that greps for "consider:" or "PROPERTY VOCABULARY" gets the same edit.

- [ ] **Step 5: Commit.**

```bash
git add artmind/domains/meta.yaml artmind/prompt_builder.py artmind/extraction.py artmind/ingest.py artmind/canonicalize.py test/
git commit -m "feat(extraction): schema-only, class-scoped properties prompt; drop the property vocabulary"
```

**Beyond the suite** (no LLM): run once on master (global `artmind`) and once with `$WT`, and record both sizes in the PR:

```bash
cd ~ && for d in banking.reference banking.policy contracts; do ARTMIND_NO_PROXY=1 uv run --project $WT artmind domains properties-prompt $d --compact 2>/dev/null | python3 -c "import json,sys; print('$d', len(json.load(sys.stdin)['prompt']))"; done
```

`properties-prompt` renders every class; the per-chunk prompt renders fewer, and §8's 50% target is measured in Task 13. Running from `~` uses `~/.artmind`'s old `meta.yaml`, so the "NO fixed schema" text still appears there; that is the vault case of question 3. Do not run `setup`.

---

### Task 4: The build-time filter

**Files:**
- Modify: `artmind/observations.py` — add `OBSERVATION_OWN_KEYS`, `declared_property_keys`, `declared_property_order`, `filter_declared` after `property_key_rules` (line ~252).
- Modify: `artmind/ingest.py` — `_build_observations` (lines ~3620–3721) uses the filter and returns the drop count; delete `_exclusive_properties` and `_strip_exclusive` (lines ~3620–3642); caller at line ~4471; log line at ~4537–4545.
- Modify: `artmind/update.py` lines ~433–452 (`write_user_chat`).
- Modify: `artmind/retime.py` lines ~79 and ~88 — swap `_strip_exclusive` for `filter_declared` now; Task 6 finishes it.
- Test: `test/test_schema_bound_properties.py` (new); `test/test_document_dates.py` (existing exclusive tests).

- [ ] **Step 1: Write the failing tests.**

```python
"""Schema-only property keys (spec 2026-11-15 §2.2). Synthetic data only."""
from artmind import ingest as ing
from artmind.observations import declared_property_keys, declared_property_order, filter_declared

SCHEMA = {
    "property_aliases": {"outcome_or_result": "outcome"},
    "entity_types": {
        "EVENT": {"kind": "occurrent", "properties": {
            "date_or_time": {"temporal": "valid_from"}, "outcome": {"required": True}, "cause": {}}},
        "PERSON": {"kind": "recurrent", "properties": {"role": {}}},
        "PLACE": {"kind": "recurrent"},
    },
}


def test_declared_property_keys_per_class():
    assert declared_property_keys(SCHEMA) == {
        "EVENT": {"date_or_time", "outcome", "cause"}, "PERSON": {"role"}, "PLACE": set()}


def test_declared_order_puts_required_first_and_can_drop_temporal():
    assert declared_property_order(SCHEMA)["EVENT"] == ["outcome", "date_or_time", "cause"]
    assert declared_property_order(SCHEMA, include_temporal=False)["EVENT"] == ["outcome", "cause"]


def test_filter_keeps_declared_structural_and_reserved_keys_and_returns_the_rest():
    obs = {"id": "o1", "name": "N", "entity_class": "EVENT", "_valid_from": "2026-01-07",
           "outcome": "won", "mood": "calm", "role": "x"}
    kept, dropped = filter_declared(obs, "EVENT", declared_property_keys(SCHEMA))
    assert kept == {"id": "o1", "name": "N", "entity_class": "EVENT", "_valid_from": "2026-01-07", "outcome": "won"}
    assert dropped == {"mood": "calm", "role": "x"}


def test_build_observations_drops_undeclared_keys_after_alias_folding():
    entities = [{"id": "e1", "name": "Call", "entity_class": "EVENT", "chunk_id": "d_001", "_domain": "j"}]
    props = [{"id": "e1", "properties": {"outcome_or_result": "agreed", "sentiment": "warm"}}]
    document = {"id": "d", "version": 1, "_valid_from": "2026-01-07"}
    obs, dropped = ing._build_observations(entities, props, {}, SCHEMA, document)
    assert obs[0]["outcome"] == "agreed"
    assert "sentiment" not in obs[0] and "outcome_or_result" not in obs[0]
    assert dropped == {"EVENT": {"sentiment": 1}}
```

- [ ] **Step 2: Run and confirm failure.**

Run: `uv run --group dev pytest test/test_schema_bound_properties.py -v`
Expected: `ImportError: cannot import name 'declared_property_keys'`.

- [ ] **Step 3: Implement.** In `artmind/observations.py`:

```python
#: Observation keys that are never domain properties: build_observation's own fields plus the
#: provenance keys table2graph and `artmind update` add. `_`-prefixed keys are always kept too.
OBSERVATION_OWN_KEYS = _STRUCTURAL_KEYS | {"source_kind", "created_by"}


def declared_property_keys(schema: dict) -> dict[str, set[str]]:
    """{class: the property keys it declares} -- the one reading of "declared" (spec 2026-11-15 §2.2)."""
    return {
        str(cls): {str(p) for p in ((decl or {}).get("properties") or {})}
        for cls, decl in (schema.get("entity_types") or {}).items() if isinstance(decl, dict)
    }


def declared_property_order(schema: dict, *, include_temporal: bool = True) -> dict[str, list[str]]:
    """{class: declared keys in schema order, `required: true` keys first}."""
    out: dict[str, list[str]] = {}
    for cls, decl in (schema.get("entity_types") or {}).items():
        if not isinstance(decl, dict):
            continue
        items = [(str(p), pd if isinstance(pd, dict) else {}) for p, pd in (decl.get("properties") or {}).items()]
        items = [(p, pd) for p, pd in items if include_temporal or not pd.get("temporal")]
        out[str(cls)] = ([p for p, pd in items if pd.get("required") is True]
                         + [p for p, pd in items if pd.get("required") is not True])
    return out


def filter_declared(obs: dict, entity_class: str, declared: dict[str, set[str]]) -> tuple[dict, dict]:
    """(`obs` with only the class's declared domain keys, {dropped key: value}). Own and `_` keys stay."""
    allowed = declared.get(entity_class, set())
    kept, dropped = {}, {}
    for k, v in obs.items():
        if k in allowed or k in OBSERVATION_OWN_KEYS or str(k).startswith(RESERVED_PREFIX):
            kept[k] = v
        else:
            dropped[k] = v
    return kept, dropped
```

In `artmind/ingest.py`:
- delete `_exclusive_properties` and `_strip_exclusive` (question 1);
- `_build_observations` returns `(observations, dropped)`, `dropped` being `{class: {key: count}}`;
- before the loop: `declared = declared_property_keys(schema)` and `dropped: dict[str, dict[str, int]] = {}`; remove the `exclusive` line;
- the `domain_props` assignment becomes:

```python
        cls = entity.get("entity_class") or ""
        domain_props, gone = filter_declared(
            normalize_property_keys(dict(props_by_id.get(entity.get("id"), {})), *key_rules), cls, declared,
        )
        for key in gone:
            dropped.setdefault(cls, {}).setdefault(key, 0)
            dropped[cls][key] += 1
```

At the caller (~line 4471): `all_observations, dropped_props = _build_observations(...)`. The "KG extraction done" log line gains `dropped_property_values=N`, where N is the sum of counts (never values). When `dropped_props` is non-empty, also set `file_result["dropped_property_values"] = dropped_props`.

In `update.write_user_chat`, just before `build_observation(...)`:

```python
            domain_props, _ = filter_declared(dict(entity_data.get("properties") or {}), entity_class,
                                              declared_property_keys(schema))
```

and pass `domain_props=domain_props`. Import both helpers from `artmind.observations`.

In `retime.py`, replace the `_exclusive_properties`/`_strip_exclusive` pair with `filter_declared(obs, cls, declared_property_keys(schema))[0]`.

Run `rg -n "_build_observations\(|_strip_exclusive|_exclusive_properties" artmind test` and fix every hit.

- [ ] **Step 4: Run and confirm.**

Run: `uv run --group dev pytest test/test_schema_bound_properties.py test/test_document_dates.py test/test_update.py test/test_ingest_provenance.py test/test_property_keys.py -v && just dev-test`

Expected:
- the exclusive tests in `test_document_dates.py` still pass (PERSON does not declare `date_or_time`, so the filter drops it);
- `test_a_non_exclusive_property_is_left_alone` **fails by design**: an undeclared key is dropped whatever `exclusive` says. Rename it `test_a_key_another_class_declares_is_dropped_from_one_that_does_not`, assert `"date_or_time" not in PERSON`, and point its docstring at spec §2.2;
- other fixtures that carry undeclared props on purpose must declare them in their schema. Never relax the filter to make a test pass.

- [ ] **Step 5: Commit.**

```bash
git add artmind/observations.py artmind/ingest.py artmind/update.py artmind/retime.py test/
git commit -m "feat(extraction): keep only schema-declared property keys at build time"
```

**Beyond the suite:** the real effect shows in Task 6's retime dry run. One real extraction is part of Task 13.

---

### Task 5: Classes with no declared property make no properties call

**Files:**
- Modify: `artmind/ingest.py` — once per document, next to where `schema` is loaded (~line 3830); the properties block (~lines 4132–4174).
- Test: `test/test_schema_bound_properties.py` (append).

- [ ] **Step 1: Write the failing tests.** They reuse the extraction harness from `test/test_document_dates.py`.

```python
NO_PROPS_SCHEMA = """\
name: journal
subject: a journal
entity_types:
  EVENT:
    kind: occurrent
    description: Something that happened.
    properties:
      outcome: {}
  PLACE:
    kind: recurrent
    description: A place.
"""


def _run_extract(tmp_path, monkeypatch, entities):
    from test_document_dates import _stub_llm, _vault

    doc = _vault(tmp_path, monkeypatch, "2026-01-07.md", "At the park.\n")
    (ing.DOMAIN_SCHEMAS_DIR / "journal_schema.yaml").write_text(NO_PROPS_SCHEMA, encoding="utf-8")
    _stub_llm(monkeypatch)
    steps, shown = [], []

    def llm(step, model, prompt, debug_dir):
        steps.append(step)
        return (entities, True) if step.endswith("_entities") else ([], True)

    monkeypatch.setattr(ing, "_llm_extract", llm)
    monkeypatch.setattr(ing, "build_properties_prompt",
                        lambda text, ents, schema, anchor_date=None, **k: shown.append(ents) or "p")
    result = ing.ingest_file(doc, "gemma4:e4b", "journal", chunk_size=6000)
    ing.extract_kg(result, "journal", max_workers=1)
    return steps, shown, ing._get_chunk_statuses(result["sha256"])[1]


def test_a_chunk_with_only_property_less_classes_makes_no_call(tmp_path, monkeypatch):
    steps, _, status = _run_extract(tmp_path, monkeypatch, [{"id": "e1", "entity_class": "PLACE", "name": "Park"}])
    assert not any(s.endswith("_properties") for s in steps)
    assert status["properties_status"] == "skipped"


def test_only_entities_of_classes_with_properties_are_sent(tmp_path, monkeypatch):
    _, shown, status = _run_extract(tmp_path, monkeypatch, [
        {"id": "e1", "entity_class": "PLACE", "name": "Park"}, {"id": "e2", "entity_class": "EVENT", "name": "Walk"}])
    assert [[e["entity_class"] for e in ents] for ents in shown] == [["EVENT"]]
    assert status["properties_status"] == "ok"
```

- [ ] **Step 2: Run and confirm failure.**

Run: `uv run --group dev pytest test/test_schema_bound_properties.py -k "property_less or only_entities" -v`
Expected: a `chunk_001_properties` step runs and PLACE is sent.

- [ ] **Step 3: Implement.** Once per document, next to where `schema` is loaded:

```python
    from artmind.observations import declared_property_keys
    classes_with_props = {c for c, keys in declared_property_keys(schema).items() if keys}
```

In the properties block, after the `redo_property_classes` narrowing (which stays until Task 11):

```python
            prompt_entities = [e for e in prompt_entities if e.get("entity_class") in classes_with_props]
```

Then replace the existing `if not prompt_entities:` branch:

```python
            if not prompt_entities:
                # Spec 2026-11-15 §2.3: no entity here belongs to a class that declares a property
                # (or, in a scoped redo, none is in scope and the cached properties stand).
                marks["properties"] = "ok" if redo_property_classes is not None else "skipped"
                data.setdefault("properties", [])
```

- [ ] **Step 4: Run and confirm.**

Run: `uv run --group dev pytest test/test_schema_bound_properties.py test/test_ingest_extract_kg_retry.py test/test_extraction_loss_guard.py test/test_chunks_only_extract.py test/test_document_context_wiring.py -v`
Expected: PASS. The failed-step guard (`ingest.py:280`) treats `skipped` as done.

- [ ] **Step 5: Commit.**

```bash
git add artmind/ingest.py test/test_schema_bound_properties.py
git commit -m "feat(extraction): no properties call for classes that declare no property"
```

---

### Task 6: `ingest retime` prunes undeclared keys from staging

**Files:**
- Modify: `artmind/retime.py` — `retime_observations` (lines ~67–99) returns dropped values too; `retime_folder` (lines ~102–140) writes the sidecar and handles a missing markdown; `_effect` (lines ~247–270) gains the drop change; `retime_domain` (lines ~273–346) collects drops.
- Modify: `artmind/cli_conventions.py` lines ~330–338 — add `property_values_dropped` and `property_backfill` (used by Task 10) to `EFFECT_KINDS`.
- Modify: `artmind/cli.py` lines ~1704–1720 (`ingest_retime` help text).
- Test: `test/test_retime_properties.py` (new); `test/test_dry_run_ingest_retime.py` (append).

- [ ] **Step 1: Write the failing tests.** `test/test_retime_properties.py`:

```python
"""`ingest retime` drops undeclared property keys from staging (spec 2026-11-15 §2.4). Synthetic data."""
import json

import yaml

import artmind.ingest as ing
from artmind.retime import retime_folder, retime_observations
from test_document_dates import EXCLUSIVE_SCHEMA, _extract, _folder, _two_class_llm, _vault

WITH_MOOD = EXCLUSIVE_SCHEMA.replace("      role: {}\n", "      role: {}\n      mood: {}\n")


def test_retime_observations_drops_and_reports_undeclared_keys():
    schema = {"entity_types": {"EVENT": {"kind": "occurrent", "properties": {"outcome": {}}}}}
    obs = [{"id": "o1", "entity_class": "EVENT", "outcome": "won", "mood": "calm", "chunk_id": "c"}]
    new, dropped = retime_observations(obs, {"_valid_from": "2026-01-07"}, schema)
    assert "mood" not in new[0] and new[0]["outcome"] == "won"
    assert dropped == {"o1": {"mood": "calm"}}


def test_table_observations_are_left_alone():
    schema = {"entity_types": {"ROW": {"kind": "recurrent", "properties": {}}}}
    obs = [{"id": "o1", "entity_class": "ROW", "source_kind": "table", "amount": 3}]
    new, dropped = retime_observations(obs, {}, schema)
    assert new[0]["amount"] == 3 and dropped == {}


def _staged_with_mood(tmp_path, monkeypatch):
    doc = _vault(tmp_path, monkeypatch, "2026-01-07.md", "Call with Srinath.\n")
    monkeypatch.setattr(ing, "LOGS_DIR", tmp_path / "logs")
    schema_file = ing.DOMAIN_SCHEMAS_DIR / "journal_schema.yaml"
    schema_file.write_text(WITH_MOOD)
    _two_class_llm(monkeypatch)
    _extract(doc)
    folder = _folder("2026-01-07")
    obs = json.loads((folder / "observations.json").read_text())
    (folder / "observations.json").write_text(json.dumps([{**o, "mood": "calm"} for o in obs]))
    schema_file.write_text(EXCLUSIVE_SCHEMA)  # mood is trimmed from the schema
    return folder


def test_retime_folder_moves_dropped_values_to_logs(tmp_path, monkeypatch):
    folder = _staged_with_mood(tmp_path, monkeypatch)
    result = retime_folder(folder, yaml.safe_load(EXCLUSIVE_SCHEMA))
    assert result["changed"] and result["dropped"] == {"mood": 2}
    assert all("mood" not in o for o in json.loads((folder / "observations.json").read_text()))
    (sidecar,) = (tmp_path / "logs" / "retime-dropped" / "journal").glob("*.json")
    assert {v["mood"] for v in json.loads(sidecar.read_text())["values"].values()} == {"calm"}


def test_a_dry_run_counts_but_moves_nothing(tmp_path, monkeypatch):
    folder = _staged_with_mood(tmp_path, monkeypatch)
    before = (folder / "observations.json").read_bytes()
    assert retime_folder(folder, yaml.safe_load(EXCLUSIVE_SCHEMA), write=False)["dropped"] == {"mood": 2}
    assert (folder / "observations.json").read_bytes() == before
    assert not (tmp_path / "logs" / "retime-dropped").exists()


def test_missing_markdown_still_prunes_and_keeps_the_dates(tmp_path, monkeypatch):
    folder = _staged_with_mood(tmp_path, monkeypatch)
    (tmp_path / "vault" / "journal" / "2026-01-07.md").unlink()
    before = json.loads((folder / "document.json").read_text())
    result = retime_folder(folder, yaml.safe_load(EXCLUSIVE_SCHEMA))
    assert result["dates"] == "kept (markdown not found)" and result["dropped"] == {"mood": 2}
    assert json.loads((folder / "document.json").read_text()) == before
```

Append to `test/test_dry_run_ingest_retime.py`:

```python
def test_dry_run_names_the_property_values_it_would_drop(tmp_path, monkeypatch):
    folder, _ = _ingested_under_the_old_schema(tmp_path, monkeypatch)
    _stub_commit(monkeypatch)
    _run()  # dates settled
    obs = json.loads((folder / "observations.json").read_text())
    (folder / "observations.json").write_text(json.dumps([{**o, "mood": "calm"} for o in obs]))
    dry = _run("--dryRun")
    real = _run()
    assert {"kind": "property_values_dropped", "count": 1, "sample": ["mood"]} in dry["effect"]["changes"]
    assert real["effect"] == dry["effect"]
```

- [ ] **Step 2: Run and confirm failure.**

Run: `uv run --group dev pytest test/test_retime_properties.py test/test_dry_run_ingest_retime.py -v`
Expected: `retime_observations` returns a list, not a tuple.

- [ ] **Step 3: Implement.** In `retime.py`:

```python
def retime_observations(observations: list[dict], document: dict, schema: dict) -> tuple[list[dict], dict]:
    """(`observations` with valid time re-derived and undeclared keys dropped, {observation id:
    {dropped key: value}}): the decisions `ingest._build_observations` + `build_observation` make.
    A table row's observation (`source_kind: table`) keeps its mapping's keys (table2graph is out
    of scope, spec 2026-11-15)."""
    from artmind.observations import declared_property_keys, filter_declared
    from artmind.temporal import _entity_temporal_mapping

    entity_dates = _entity_temporal_mapping(schema)
    declared = declared_property_keys(schema)
    key_rules = property_key_rules(schema)
    doc_from, doc_to = document.get("_valid_from"), document.get("_valid_to")
    out, dropped = [], {}
    for obs in observations:
        cls = obs.get("entity_class") or ""
        obs = normalize_property_keys(obs, *key_rules)
        if obs.get("source_kind") != "table":
            obs, gone = filter_declared(obs, cls, declared)
            if gone:
                dropped[obs.get("id") or ""] = gone
        fact_from, fact_to = ingest._fact_valid_time(entity_dates, cls, obs)
        new = {k: v for k, v in obs.items() if k not in _OBSERVATION_TIME_KEYS}
        times = {
            "_valid_from": fact_from or doc_from,
            "_valid_to": fact_to or doc_to,
            "_valid_time_source": "property" if fact_from else document.get("_valid_time_source"),
            "_doc_valid_from": doc_from,
        }
        new.update({k: v for k, v in times.items() if v})
        out.append(new)
    return out, dropped


def _keep_dropped(doc_kg_dir: Path, name: str, dropped: dict) -> None:
    """Move dropped values beside the run (private, gitignored), merged with earlier runs."""
    sidecar = ingest.LOGS_DIR / "retime-dropped" / doc_kg_dir.parent.name / f"{doc_kg_dir.name}.json"
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    prior = _load_json(sidecar, {}) or {}
    payload = {"document": name, "values": {**(prior.get("values") or {}), **dropped}}
    tmp = sidecar.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(sidecar)
```

In `retime_folder`:
- **Missing markdown (question 16):** when `md_file` does not exist, set `new_document = document` and `dates_note = "kept (markdown not found)"`, and carry on instead of returning `skipped`. Otherwise compute the dates as today and set `dates_note = None`.
- **Unpack:** `new_observations, dropped = retime_observations(observations, new_document, schema)`; `counts = Counter(k for d in dropped.values() for k in d)`.
- **Write:** when `changed and write`, call `_keep_dropped(doc_kg_dir, name, dropped)` **before** `ingest.write_staging(...)` if there are drops.
- **Return:** add `"dropped": dict(counts)`, and `"dates": dates_note` when set.

In `_effect`: add a `dropped_keys: Counter` parameter. When it is non-empty, append `Change("property_values_dropped", sum(dropped_keys.values()), [k for k, _ in dropped_keys.most_common()])`, and the summary adds "; drop N undeclared property value(s), kept under logs/retime-dropped/".

In `retime_domain`: accumulate `dropped_keys.update(result.get("dropped") or {})`, pass it to `_effect`, and add `"dropped": dict(dropped_keys)` to the report.

`ingest_retime` help: after the first paragraph, add "It also drops property keys the observation's class does not declare; their values are kept under logs/retime-dropped/."

Run `rg -n "retime_observations\(" artmind test` and fix every caller.

- [ ] **Step 4: Run and confirm.**

Run: `uv run --group dev pytest test/test_retime_properties.py test/test_dry_run_ingest_retime.py test/test_document_dates.py test/test_cli_conventions.py -v`
Expected: PASS.

- [ ] **Step 5: Commit.**

```bash
git add artmind/retime.py artmind/cli_conventions.py artmind/cli.py test/test_retime_properties.py test/test_dry_run_ingest_retime.py
git commit -m "feat(retime): drop undeclared property keys from staging, values kept under logs/"
```

**Beyond the suite** (no LLM, no writes):

```bash
cd ~/artmind_vaults/banking_corpus && ARTMIND_NO_PROXY=1 uv run --project $WT artmind ingest retime --domain banking.reference --dryRun --compact | python3 -c "import json,sys; e=json.load(sys.stdin)['effect']; print([c for c in e['changes'] if c['kind']=='property_values_dropped'])"
cd ~/artmind_vaults/brain01 && ARTMIND_NO_PROXY=1 uv run --project $WT artmind ingest retime --domain personal_journal --dryRun --compact | python3 -c "import json,sys; r=json.load(sys.stdin); print(r['scanned'], r['changed'], sum(r['dropped'].values()), len(r['dropped']))"
```

Expected: a distinct-key count close to spec §1.1 (571 − 81 in brain01). Record counts only. Do **not** run without `--dryRun`: that is the per-vault rollout, after the schema edits, with the user's go-ahead.

---

## Part 3 — Declared property values on brief rows (spec §5)

### Task 7: `brief.entity_line` carries declared property values

**Files:**
- Modify: `artmind/brief.py` lines ~340–431 (`entity_line`, `pattern_row`, `entity_context_row`; new constants).
- Modify: `artmind/graph_query.py` — pattern brief (lines ~1278–1282), entity-context brief (lines ~1989–1993), new `_declared_orders` and `_declared_for`.
- Test: `test/test_graph_brief.py` (append; patch its `_env` fixture).

- [ ] **Step 1: Write the failing tests.** Add one line to the autouse `_env` fixture in `test/test_graph_brief.py`, so existing tests are unaffected:

```python
    monkeypatch.setattr(gq, "_declared_orders", lambda domains: {})
```

Append:

```python
ORDER = {"ORG": ["sector", "status", "recent_activity"]}


def test_entity_line_carries_declared_values_in_order():
    e = {**_entity(), "sector": "widgets", "status": "active", "mood": "calm", "_valid_from": "2024-01-01"}
    line = brief.entity_line(e, declared=ORDER["ORG"])
    assert list(line["properties"]) == ["sector", "status", "recent_activity"]
    assert "mood" not in line["properties"] and "_valid_from" not in line["properties"]


def test_a_long_list_keeps_its_newest_items_within_the_budget():
    e = {**_entity(), "recent_activity": [f"activity number {n} of a long list" for n in range(60)]}
    line = brief.entity_line(e, declared=ORDER["ORG"], budget=300)
    shown = line["properties"]["recent_activity"]
    assert shown[-1] == "activity number 59 of a long list"
    assert len(json.dumps(line["properties"], ensure_ascii=False, separators=(",", ":"))) <= 300
    assert line["truncated_properties"]["recent_activity"] == 60


def test_no_declared_order_means_no_properties_key():
    assert "properties" not in brief.entity_line(_entity())


def test_pattern1_brief_shows_declared_values_within_the_listing_budget(monkeypatch):
    rows = [{"entityData": {**_entity(1), "_domain": "d", "sector": "widgets"}, "_rowsTotal": 1}]
    monkeypatch.setattr(gq, "_declared_orders", lambda domains: {"d": ORDER})
    out = _run_pattern(monkeypatch, ["pattern1", "--entityClass", "ORG", "--shape", "brief"], rows)
    props = out["rows"][0]["properties"]
    assert props["sector"] == "widgets"
    assert len(json.dumps(props, ensure_ascii=False, separators=(",", ":"))) <= brief.LISTING_PROPERTY_CHARS


def test_entity_context_brief_hub_with_a_full_property_bag_fits_the_agent_budget(monkeypatch):
    responses, max_conns, include = _hub()
    bag = {f"prop_{i}": "a long declared value " * 8 for i in range(40)}
    entity = {**responses["entity"][0]["entityData"], "_domain": "d", **bag,
              "recent_activity": [f"item {n}" for n in range(300)]}
    responses["entity"] = [{"entityData": entity}]
    monkeypatch.setattr(gq, "_declared_orders", lambda domains: {"d": {"ORG": [*bag, "recent_activity"]}})
    _install_session(monkeypatch, _RecordingSession(responses))
    out = gq.entity_context(["d"], "e1", include_chunks=include, max_connections=max_conns, shape="brief")
    row = out["rows"][0]
    assert len(json.dumps(row["entity"]["properties"], ensure_ascii=False, separators=(",", ":"))) \
        <= brief.ENTITY_PROPERTY_CHARS
    assert row["entity"]["truncated_properties"]
    size = len(json.dumps(out, ensure_ascii=False))
    print(f"hub entity-context with a full property bag: brief={size}")
    assert size < 24000
```

- [ ] **Step 2: Run and confirm failure.**

Run: `uv run --group dev pytest test/test_graph_brief.py -v`
Expected: `gq._declared_orders` does not exist yet, and `entity_line` takes no `declared`.

- [ ] **Step 3: Implement.** In `brief.py` (the existing `size()` uses compact separators):

```python
#: Property characters a brief row may carry (spec 2026-11-15 §5; plan question 9).
ENTITY_PROPERTY_CHARS = 600
LISTING_PROPERTY_CHARS = 200
_LISTING_PATTERNS = frozenset({"pattern1", "pattern7", "pattern8"})


def _bounded_properties(entity: dict, declared: list[str], budget: int) -> tuple[dict, dict]:
    """Declared values in `declared` order within `budget` characters (compact JSON). A list keeps
    its newest (last) items; a scalar that does not fit is left out. Returns (properties,
    {key: full length, 1 for a scalar left out})."""
    shown, cut_off = {}, {}
    for key in declared:
        value = entity.get(key)
        if value in (None, "", []):
            continue
        if isinstance(value, list):
            kept: list = []
            for item in reversed(value):
                if size({**shown, key: [item, *kept]}) > budget:
                    break
                kept.insert(0, item)
            if len(kept) < len(value):
                cut_off[key] = len(value)
            if kept:
                shown[key] = kept
        elif size({**shown, key: value}) <= budget:
            shown[key] = value
        else:
            cut_off[key] = 1
    return shown, cut_off


def entity_line(entity: dict, declared: list[str] | None = None, budget: int = ENTITY_PROPERTY_CHARS) -> dict:
    """id, name, class and a one-line description of an entity's data (`entityData`), plus its
    declared non-temporal property values when `declared` (the class's order) is given."""
    entity = entity or {}
    out = {"id": entity.get("_id") or entity.get("id"), "name": entity.get("name"),
           "class": entity.get("entity_class"),
           "description": one_line(entity.get("description") or entity.get("summary"))}
    if declared:
        props, cut_off = _bounded_properties(entity, declared, budget)
        if props:
            out["properties"] = props
        if cut_off:
            out["truncated_properties"] = cut_off
    return out
```

`pattern_row(pattern, row, declared=None)`: its first line becomes

```python
    out = entity_line(row.get("entityData") or {}, declared,
                      LISTING_PROPERTY_CHARS if pattern in _LISTING_PATTERNS else ENTITY_PROPERTY_CHARS)
```

`entity_context_row(row, declared=None)`: build `out = {"entity": entity_line(row.get("entityData") or {}, declared)}`. If the row carries `truncated_properties` from `_cap_entity_lists`, merge its counts into `out["entity"]["truncated_properties"]` (larger count per key) when `declared` is given. Keep the top-level `truncated_properties` key: `entity_context_cut` reads it.

In `graph_query.py`:

```python
def _declared_orders(domains) -> dict[str, dict[str, list[str]]]:
    """{domain: {class: declared non-temporal keys, required first}} for brief rows, read from this
    machine's schemas (spec 2026-11-15 §5). A family root also loads its children."""
    from paths import DOMAIN_SCHEMAS_DIR
    from artmind.observations import declared_property_order
    from artmind.temporal import load_schema

    out: dict = {}
    for d in normalize_domains(domains):
        names = [d] + [p.name.removesuffix("_schema.yaml") for p in sorted(DOMAIN_SCHEMAS_DIR.glob(f"{d}.*_schema.yaml"))]
        for name in names:
            out[name] = declared_property_order(load_schema(name, schemas_dir=DOMAIN_SCHEMAS_DIR), include_temporal=False)
    return out


def _declared_for(orders: dict, entity: dict | None) -> list[str] | None:
    entity = entity or {}
    return (orders.get(entity.get("_domain") or "") or {}).get(entity.get("entity_class") or "")
```

Pattern brief branch: `orders = _declared_orders(domains)` and `rows = [brief.pattern_row(pattern, r, _declared_for(orders, r.get("entityData"))) for r in rows]`. Entity-context brief branch: the same with `brief.entity_context_row`. Check `DOMAIN_SCHEMAS_DIR` is importable in `graph_query.py` (the function imports it from `paths` itself).

- [ ] **Step 4: Run and confirm.**

Run: `uv run --group dev pytest test/test_graph_brief.py test/test_brief_shape.py test/test_query_layer.py test/test_graph_query.py -v -s -k "brief or entity_context or pattern"`
Expected: PASS. The hub test prints its brief size; record it in the PR.

- [ ] **Step 5: Commit.**

```bash
git add artmind/brief.py artmind/graph_query.py test/test_graph_brief.py
git commit -m "feat(brief): brief entity rows carry declared property values within a budget"
```

**Beyond the suite** (read-only; needs Neo4j; banking_corpus is public, so values may be printed):

```bash
cd ~/artmind_vaults/banking_corpus && ARTMIND_NO_PROXY=1 uv run --project $WT artmind query graph pattern1 --domain banking.reference --entityClass RATE_ENTRY --shape brief --compact | python3 -c "import json,sys; o=json.load(sys.stdin); print(len(json.dumps(o)), o['rows'][0])"
```

Expect `properties` with `rate_value` and the other declared keys, and a payload well under 24,000 characters. Then run `query entity-context --shape brief --entityId <that row's id>` and check `entity.properties`. Regenerate the plugin fixtures with `ARTMIND_REGEN_PLUGIN_FIXTURES=1 uv run --group dev pytest test/test_plugin_fixtures.py && git status obsidian/`; no fixture is a brief graph row, so expect no diff.

---

## Part 4 — The property audit (spec §3)

### Task 8: `artmind/property_audit.py`

**Files:**
- Create: `artmind/property_audit.py`.
- Test: `test/test_property_audit.py`.

- [ ] **Step 1: Write the failing tests.**

```python
"""`domains property-audit` rules (spec 2026-11-15 §3). Synthetic staging only."""
import json
from types import SimpleNamespace

import pytest

from artmind import property_audit as pa

SCHEMA = {"entity_types": {"EVENT": {"kind": "occurrent", "properties": {
    "date_or_time": {"temporal": "valid_from"}, "outcome": {}, "cause": {}, "mood": {},
    "rare": {}, "kept": {"keep_reason": "probe M04"}, "must": {"required": True}}}}}


def _stage(root, observations, chunk_text):
    folder = root / "kg" / "d" / "doc1"
    folder.mkdir(parents=True)
    (folder / "document.json").write_text(json.dumps({"id": "doc1", "name": "doc1.md"}))
    (folder / "chunks.json").write_text(json.dumps([{"id": "c1", "text": chunk_text}]))
    (folder / "observations.json").write_text(json.dumps(observations))
    return root / "kg"


def _obs(i, **props):
    return {"id": f"o{i}", "entity_class": "EVENT", "chunk_id": "c1",
            "description": "a team call about the budget", **props}


def _rows(kg, depended_on=None):
    return {(r["class"], r["key"]): r for r in pa.audit("d", SCHEMA, kg_dir=kg, depended_on=depended_on or {})["rows"]}


@pytest.fixture()
def rows(tmp_path):
    observations = [_obs(i, date_or_time="2026-01-07", outcome="budget agreed", must="x",
                         mood="quietly hopeful and reflective", cause="the budget call") for i in range(20)]
    observations[0]["rare"] = "once"
    observations[1]["invented"] = "y"
    return _rows(_stage(tmp_path, observations, "The budget call went well; budget agreed."))


def test_undeclared_key_is_drop(rows):
    assert rows[("EVENT", "invented")]["proposal"] == "drop" and rows[("EVENT", "invented")]["declared"] is False


def test_temporal_key_is_keep(rows):
    assert rows[("EVENT", "date_or_time")]["proposal"] == "keep"
    assert rows[("EVENT", "date_or_time")]["flags"]["temporal"] == "valid_from"


def test_declared_required_reports_required(rows):
    assert rows[("EVENT", "must")]["proposal"] == "required"


def test_keep_reason_wins_over_the_drop_rules(rows):
    assert rows[("EVENT", "kept")]["proposal"] == "keep" and "probe M04" in rows[("EVENT", "kept")]["reason"]


def test_full_fill_is_required(rows):
    assert rows[("EVENT", "outcome")]["fill"] == 1.0 and rows[("EVENT", "outcome")]["proposal"] == "required"


def test_interpretive_values_are_drop(tmp_path):
    observations = [_obs(i, mood="quietly hopeful and reflective") for i in range(10)] + [_obs(10 + i) for i in range(2)]
    row = _rows(_stage(tmp_path, observations, "The call happened."))[("EVENT", "mood")]
    assert row["inferred"] >= 0.6 and row["text_only"] < 0.1 and row["proposal"] == "drop"


def test_rarely_stated_is_drop(rows):
    assert rows[("EVENT", "rare")]["fill"] == 0.05 and rows[("EVENT", "rare")]["proposal"] == "drop"


def test_depended_on_is_keep(tmp_path):
    kg = _stage(tmp_path, [_obs(1, rare="x")] + [_obs(i) for i in range(2, 40)], "text")
    row = _rows(kg, {("EVENT", "rare"): ["mart"]})[("EVENT", "rare")]
    assert row["proposal"] == "keep" and row["flags"]["depended_on"] == ["mart"]


def test_payload_lists_no_values_and_carries_the_rules(tmp_path):
    out = pa.audit("d", SCHEMA, kg_dir=_stage(tmp_path, [_obs(1, mood="secret words")], "text"), depended_on={})
    assert "secret" not in json.dumps(out)
    assert out["rules"]["required_fill"] == pa.REQUIRED_FILL


def test_table_observations_are_not_counted(tmp_path):
    kg = _stage(tmp_path, [{**_obs(1, amount=3), "source_kind": "table"}], "text")
    assert ("EVENT", "amount") not in _rows(kg)


def test_depended_on_keys_reads_the_mart_declaration(monkeypatch):
    decl = SimpleNamespace(person=SimpleNamespace(entity_class="PERSON", seed_property="relation"),
                           place=SimpleNamespace(entity_class="LOCATION", seed_property=None),
                           type=SimpleNamespace(classes=["EVENT"], property="type"),
                           happening_classes=["EVENT"], wholes={})
    monkeypatch.setattr("artmind.mart.declaration.load_declaration", lambda d: decl)
    monkeypatch.setattr("artmind.kg_views.store.list_views", lambda domains=None: ([], []))
    deps = pa.depended_on_keys("d", {"entity_types": {"PERSON": {"properties": {"relation": {}}}, "EVENT": {}}})
    assert deps[("PERSON", "relation")] == ["mart"]


def test_graph_conflicts_is_one_read_query_scoped_to_the_domain(monkeypatch):
    from _neo4j_recorder import Recorder

    rec = Recorder(respond=lambda cypher, params: [{"cls": "EVENT", "key": "mood", "n": 4}])
    monkeypatch.setattr("artmind.graph_query.read_session", lambda: rec)
    assert pa.graph_conflicts("d") == {("EVENT", "mood"): 4}
    ((cypher, params),) = rec.calls
    assert "Conflict" in cypher and "MERGE" not in cypher and params["domains"] == ["d"]
```

- [ ] **Step 2: Run and confirm failure.**

Run: `uv run --group dev pytest test/test_property_audit.py -v`
Expected: `ModuleNotFoundError: No module named 'artmind.property_audit'`.

- [ ] **Step 3: Implement** `artmind/property_audit.py`:

```python
"""`artmind domains property-audit`: per (class, key) evidence for keeping a property (spec
2026-11-15 §3). Read-only: staging files, schema, mart declaration, insights config and saved views;
Neo4j only with --graph. Lists key names, counts and shares -- never a value."""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from artmind.atomic_dir import is_scratch
from artmind.observations import OBSERVATION_OWN_KEYS, RESERVED_PREFIX

REQUIRED_FILL = 0.95
INFERRED_DROP = 0.6
TEXT_ONLY_MAX = 0.1
RARE_FILL = 0.10
DISTINCT_CAP = 1000
_WORD = re.compile(r"[a-z0-9]+")
_BACKTICKED = re.compile(r"`(?:([A-Z][A-Z0-9_]*)\.)?([a-z][a-z0-9_]*)`")


def _words(value) -> list[str]:
    items = value if isinstance(value, list) else [value]
    return [w for item in items for w in _WORD.findall(str(item).lower()) if len(w) > 2]


def _overlap(value, described: set[str], chunk: set[str]) -> tuple[int, int, int]:
    """Characters of `value` (by word) in the description/context, else only in the chunk text,
    else in neither -- spec §1.1's three shares."""
    a = b = c = 0
    for w in _words(value):
        if w in described:
            a += len(w)
        elif w in chunk:
            b += len(w)
        else:
            c += len(w)
    return a, b, c


def _load(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _folders(kg_dir: Path, domain: str) -> list[Path]:
    root = Path(kg_dir) / domain
    if not root.is_dir():
        return []
    return sorted(d for d in root.iterdir() if d.is_dir() and not is_scratch(d)
                  and not d.name.startswith("table__") and (d / "observations.json").exists())


def audit(domain: str, schema: dict, *, kg_dir: Path, depended_on: dict, conflicts: dict | None = None) -> dict:
    types = schema.get("entity_types") or {}
    decls = {c: {str(k): (v if isinstance(v, dict) else {}) for k, v in ((d or {}).get("properties") or {}).items()}
             for c, d in types.items() if isinstance(d, dict)}
    per_class: Counter = Counter()
    filled: Counter = Counter()
    chars: dict = defaultdict(lambda: [0, 0, 0])
    distinct: dict = defaultdict(set)
    for folder in _folders(kg_dir, domain):
        chunks = {c.get("id"): set(_words(c.get("text") or "")) for c in _load(folder / "chunks.json", []) or []}
        for obs in _load(folder / "observations.json", []) or []:
            if obs.get("source_kind") == "table":
                continue
            cls = obs.get("entity_class") or ""
            per_class[cls] += 1
            described = set(_words([obs.get("description") or "", *(obs.get("context") or [])]))
            chunk = chunks.get(obs.get("chunk_id"), set())
            for key, value in obs.items():
                if key in OBSERVATION_OWN_KEYS or str(key).startswith(RESERVED_PREFIX) or value in (None, "", []):
                    continue
                filled[(cls, key)] += 1
                a, b, c = _overlap(value, described, chunk)
                acc = chars[(cls, key)]
                acc[0] += a
                acc[1] += b
                acc[2] += c
                if len(distinct[(cls, key)]) < DISTINCT_CAP:
                    distinct[(cls, key)].add(" ".join(_words(value)))
    keys = {(c, k) for c, ks in decls.items() for k in ks} | set(filled)
    rows = []
    for cls, key in sorted(keys):
        decl = (decls.get(cls) or {}).get(key)
        a, b, c = chars[(cls, key)]
        total = (a + b + c) or 1
        flags: dict = {}
        if decl is not None:
            for flag in ("temporal", "exclusive"):
                if decl.get(flag):
                    flags[flag] = decl[flag]
            if decl.get("required") is True:
                flags["required"] = True
        if depended_on.get((cls, key)):
            flags["depended_on"] = sorted(depended_on[(cls, key)])
        row = {"class": cls, "key": key, "declared": decl is not None,
               "fill": round(filled[(cls, key)] / per_class[cls], 3) if per_class[cls] else 0.0,
               "in_description": round(a / total, 3), "text_only": round(b / total, 3),
               "inferred": round(c / total, 3), "distinct_values": len(distinct[(cls, key)]), "flags": flags}
        if conflicts is not None:
            row["conflicts"] = conflicts.get((cls, key), 0)
        row["proposal"], row["reason"] = _propose(row, decl)
        rows.append(row)
    return {"domain": domain, "rows": rows, "observations": dict(per_class),
            "rules": {"required_fill": REQUIRED_FILL, "inferred_drop": INFERRED_DROP,
                      "text_only_max": TEXT_ONLY_MAX, "rare_fill": RARE_FILL}}


def _propose(row: dict, decl: dict | None) -> tuple[str, str]:
    """First match wins (spec §3, with the plan's question 10 ordering)."""
    if decl is None:
        return "drop", "undeclared"
    if decl.get("required") is True:
        return "required", "declared required"
    if decl.get("keep_reason"):
        return "keep", f"keep_reason: {decl['keep_reason']}"
    if row["flags"].get("temporal"):
        return "keep", "temporal"
    if row["flags"].get("depended_on"):
        return "keep", "depended on: " + ", ".join(row["flags"]["depended_on"])
    if row["fill"] >= REQUIRED_FILL:
        return "required", f"filled on at least {REQUIRED_FILL:.0%} of observations"
    if row["inferred"] >= INFERRED_DROP and row["text_only"] < TEXT_ONLY_MAX:
        return "drop", "mostly the model's interpretation"
    if row["fill"] < RARE_FILL:
        return "drop", "rarely stated"
    return "review", "no rule decides"


def depended_on_keys(domain: str, schema: dict) -> dict[tuple[str, str], list[str]]:
    """{(class, key): [mart | insights | view:<name> | skill]} -- what names each key (plan question 11)."""
    from artmind.schema_validate import _declared, view_property_refs

    declared = _declared(schema.get("entity_types") or {})
    out: dict = defaultdict(set)
    from artmind.mart.declaration import load_declaration
    try:
        decl = load_declaration(domain)
    except Exception:
        decl = None
    if decl is not None:
        for cls, key in ((decl.person.entity_class, decl.person.seed_property),
                         (decl.place.entity_class, decl.place.seed_property)):
            if key:
                out[(cls, key)].add("mart")
        for cls in decl.type.classes:
            out[(cls, decl.type.property)].add("mart")
        for w in (decl.wholes or {}).values():
            for key in [m for m in (getattr(w, "match", None) or []) if m not in ("edge", "name")]:
                for cls in decl.happening_classes:
                    out[(cls, key)].add("mart")
    from artmind.insights.config import InsightsError, load_config
    try:
        cfg = load_config(domain)
    except InsightsError:
        cfg = None
    if cfg is not None:
        if cfg.block("outcome"):
            b = cfg.block("outcome")
            out[(b["class"], b["property"])].add("insights")
        if cfg.block("open_loops"):
            b = cfg.block("open_loops")
            out[(b["class"], b["property"])].add("insights")
            out[(b["class"], b["urgency_property"])].add("insights")
    from artmind.kg_views.store import list_views
    views, _ = list_views([domain])
    for view in views:
        for classes, key in view_property_refs(view.spec.cypher, declared):
            for cls in classes:
                out[(cls, key)].add(f"view:{view.spec.name}")
        _skill_refs(view.skill_md, declared, out)
    for text in _topic_plan_skills(domain):
        _skill_refs(text, declared, out)
    return {k: sorted(v) for k, v in out.items()}


def _skill_refs(text: str, declared: dict, out: dict) -> None:
    for cls, key in _BACKTICKED.findall(text or ""):
        for c in ([cls] if cls else [c for c, keys in declared.items() if key in keys]):
            if key in declared.get(c, set()):
                out[(c, key)].add("skill")


def _topic_plan_skills(domain: str) -> list[str]:
    """SKILL.md texts of topic plans whose `domains` (plan.yaml, a list of strings; see
    `artmind/topics/plan.py`) cover `domain`."""
    import paths
    root = Path(paths.DOMAIN_TOPIC_PLANS_DIR)
    texts = []
    for skill in sorted(root.glob("*/SKILL.md")) if root.is_dir() else []:
        plan = _load_yaml(skill.parent / "plan.yaml")
        domains = [str(d) for d in (plan.get("domains") or [])]
        if any(d == domain or d.startswith(domain + ".") or domain.startswith(d + ".") for d in domains):
            texts.append(skill.read_text(encoding="utf-8"))
    return texts


def _load_yaml(path: Path) -> dict:
    import yaml
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}


def graph_conflicts(domain: str) -> dict[tuple[str, str], int]:
    """{(class, key): open projection conflicts} for `domain` and its children (one read query)."""
    from artmind.graph_query import domain_predicate, normalize_domains, read_session

    cypher = f"""
    MATCH (c:Conflict {{_source: 'projection', status: 'open'}})-[:CONFLICT_OF]->(e:Entity)
    WHERE {domain_predicate("e")}
    RETURN e.entity_class AS cls, c.property AS key, count(*) AS n"""
    with read_session() as session:
        return {(r["cls"], r["key"]): r["n"] for r in session.run(cypher, domains=normalize_domains(domain))}
```

- [ ] **Step 4: Run and confirm.** Run: `uv run --group dev pytest test/test_property_audit.py -v`. Expected: PASS.

- [ ] **Step 5: Commit.**

```bash
git add artmind/property_audit.py test/test_property_audit.py
git commit -m "feat(audit): per (class, key) property audit with keep/drop/required proposals"
```

---

### Task 9: The `artmind domains property-audit` command

**Files:**
- Modify: `artmind/cli.py` — new command after `domains_validate` (around line 809).
- Test: `test/test_property_audit.py` (append).

- [ ] **Step 1: Write the failing test.**

```python
def test_cli_prints_the_envelope_and_next(tmp_path, monkeypatch):
    import yaml
    from click.testing import CliRunner

    import artmind.ingest as ing
    from artmind.cli import cli

    monkeypatch.setattr(ing, "KG_DIR", _stage(tmp_path, [_obs(1, outcome="x")], "text"))
    schemas = tmp_path / "schemas"
    schemas.mkdir()
    (schemas / "d_schema.yaml").write_text(yaml.safe_dump({"name": "d", **SCHEMA}))
    monkeypatch.setattr(ing, "DOMAIN_SCHEMAS_DIR", schemas)
    monkeypatch.setattr("artmind.cli.DOMAIN_SCHEMAS_DIR", schemas)
    monkeypatch.setattr(pa, "depended_on_keys", lambda d, s: {})
    monkeypatch.setattr(pa, "graph_conflicts", lambda d: (_ for _ in ()).throw(AssertionError("no Neo4j")))

    r = CliRunner().invoke(cli, ["domains", "property-audit", "--domain", "d", "--compact"])

    assert r.exit_code == 0, r.output
    out = json.loads(r.stdout)
    assert out["command"] == "domains property-audit" and out["next"] == "artmind domains validate"
    assert "conflicts" not in out["rows"][0]
```

- [ ] **Step 2: Run and confirm failure.** `uv run --group dev pytest test/test_property_audit.py -k cli -v` — expected: "No such command 'property-audit'".

- [ ] **Step 3: Implement.**

```python
@domains.command("property-audit")
@click.option("--domain", required=True, help="Domain whose staged observations to audit")
@click.option("--graph", "with_graph", is_flag=True, help="Also count open projection conflicts per key (reads Neo4j)")
@compact_option
def domains_property_audit(domain: str, with_graph: bool, compact: bool) -> None:
    """Propose keep, drop or required for each property key of a domain's classes.

    \b
    Read-only, no LLM: reads the domain's staging (observations and chunk text), its schema,
    mart declaration, insights config and saved views. Lists key names, counts and shares, never
    values. After editing the schema, `artmind ingest retime --domain D --dryRun` shows what a
    trim would drop.
    """
    from artmind import ingest as _ingest
    from artmind import property_audit
    from artmind.temporal import load_schema

    schema_file = DOMAIN_SCHEMAS_DIR / f"{domain}_schema.yaml"
    if not schema_file.exists():
        raise click.ClickException(f"Domain schema not found: {schema_file}")
    schema = load_schema(domain, schemas_dir=DOMAIN_SCHEMAS_DIR)
    report = property_audit.audit(
        domain, schema, kg_dir=_ingest.KG_DIR,
        depended_on=property_audit.depended_on_keys(domain, schema),
        conflicts=property_audit.graph_conflicts(domain) if with_graph else None,
    )
    report["next"] = "artmind domains validate"
    _echo_json(report, compact)
```

`cli_conventions.envelope` keeps a payload's own `next`. Without Neo4j, `--graph` exits 3 through `_RootGroup`.

- [ ] **Step 4: Run and confirm.** `uv run --group dev pytest test/test_property_audit.py test/test_cli_conventions.py test/test_cli_guide.py -v`. Expected: PASS. No write helper is used, so the contract test needs no `_READ_ONLY` entry.

- [ ] **Step 5: Commit.**

```bash
git add artmind/cli.py test/test_property_audit.py
git commit -m "feat(cli): artmind domains property-audit"
```

**Beyond the suite** (read-only):

```bash
cd ~/artmind_vaults/banking_corpus && ARTMIND_NO_PROXY=1 uv run --project $WT artmind domains property-audit --domain banking.reference --graph --compact | python3 -c "import json,sys; from collections import Counter; o=json.load(sys.stdin); print(Counter(r['proposal'] for r in o['rows']))"
cd ~/artmind_vaults/brain01 && ARTMIND_NO_PROXY=1 uv run --project $WT artmind domains property-audit --domain personal_journal --compact | python3 -c "import json,sys; o=json.load(sys.stdin); print([(r['class'], r['key'], r['proposal'], r['reason']) for r in o['rows'] if r['class']=='EMOTION'])"
```

Expect on brain01: EMOTION `trigger` and `resolution` come out `drop` ("mostly the model's interpretation"), as spec §3 predicts, and `keep` once the vault schema gives them `keep_reason:`. Check totals against spec §1.1 (571 distinct keys, 81 declared). Report only key names and counts.

---

## Part 5 — Targeted backfill (spec §4)

### Task 10: `artmind ingest backfill-properties`

**Files:**
- Create: `artmind/backfill.py`.
- Modify: `artmind/cli.py` — add `ingest backfill-properties` after `ingest_retime`; add `"backfill-properties"` to the "Graph building" list (line ~392).
- Modify: `artmind/cli_conventions.py` line ~62 — add `"ingest backfill-properties"` to tier 1 of `WRITE_COMMANDS`.
- Test: `test/test_backfill_properties.py`, `test/test_dry_run_ingest_backfill_properties.py` (both new).

- [ ] **Step 1: Write the failing tests.** `test/test_backfill_properties.py`:

```python
"""`ingest backfill-properties` (spec 2026-11-15 §4). Synthetic text only; the LLM is a stub."""
import json

import pytest

import artmind.ingest as ing
from artmind import backfill
from test_document_dates import JOURNAL_SCHEMA, _extract, _folder, _stub_llm, _vault

HEADER = JOURNAL_SCHEMA.split("entity_types:")[0]
SCHEMA_V1 = HEADER + """entity_types:
  EMOTION:
    kind: occurrent
    description: A feeling.
    properties:
      intensity: {}
  PERSON:
    kind: recurrent
    description: A person.
    properties:
      role: {}
"""
SCHEMA_V2 = SCHEMA_V1.replace("      intensity: {}\n", "      intensity: {}\n      trigger: {}\n")
SCHEMA_V3 = SCHEMA_V2.replace("      trigger: {}\n", "      trigger: {}\n      felt_on:\n        temporal: valid_from\n")


@pytest.fixture()
def staged(tmp_path, monkeypatch):
    doc = _vault(tmp_path, monkeypatch, "2026-01-07.md", "Felt uneasy before the review with Alex.\n")
    monkeypatch.setattr(ing, "LOGS_DIR", tmp_path / "logs")
    schema_file = ing.DOMAIN_SCHEMAS_DIR / "journal_schema.yaml"
    schema_file.write_text(SCHEMA_V1)
    _stub_llm(monkeypatch)

    def llm(step, model, prompt, debug_dir):
        if step.endswith("_entities"):
            return [{"id": "e1", "entity_class": "EMOTION", "name": "Unease"},
                    {"id": "e2", "entity_class": "PERSON", "name": "Alex"}], True
        if step.endswith("_properties"):
            return [{"id": "e1", "properties": {"intensity": "mild"}}, {"id": "e2", "properties": {"role": "lead"}}], True
        return [], True

    monkeypatch.setattr(ing, "_llm_extract", llm)
    _extract(doc)
    schema_file.write_text(SCHEMA_V2)  # trigger is declared later
    monkeypatch.setattr(ing, "commit_to_graph", lambda d, dom, defer_rebuild=False: True)
    monkeypatch.setattr(ing, "rebuild_projection", lambda domain: {})
    return _folder("2026-01-07")


def _answer(monkeypatch, answer, seen=None):
    def ask(step, model, prompt, debug_dir=None):
        if seen is not None:
            seen.append(prompt)
        return answer, True
    monkeypatch.setattr(backfill, "_ask", ask)


def _by_class(folder):
    return {o["entity_class"]: o for o in json.loads((folder / "observations.json").read_text())}


def test_fills_only_the_named_key_and_keeps_everything_else(staged, monkeypatch):
    seen = []
    _answer(monkeypatch, [{"id": "o1", "properties": {"trigger": "the review", "intensity": "IGNORED"}}], seen)
    before = _by_class(staged)

    report = backfill.backfill_domain("journal", targets={"EMOTION": ["trigger"]}, model="m")

    after = _by_class(staged)
    assert after["EMOTION"]["trigger"] == "the review"
    assert after["EMOTION"]["intensity"] == before["EMOTION"]["intensity"], "an unnamed key is never touched"
    assert after["PERSON"] == before["PERSON"]
    assert after["EMOTION"]["canonical_name"] == before["EMOTION"]["canonical_name"]
    blocks = seen[0].split("__list_of_entities__")[0]
    assert "For EMOTION, extract:" in blocks and "- trigger" in blocks and "intensity" not in blocks
    assert "PERSON" not in seen[0]
    assert report["committed"] == 1 and report["filled"] == 1


def test_an_undeclared_property_is_refused(staged):
    with pytest.raises(ValueError, match="mood not declared on EMOTION"):
        backfill.backfill_domain("journal", targets={"EMOTION": ["mood"]}, model="m")


def test_a_rerun_uses_the_cache(staged, monkeypatch):
    _answer(monkeypatch, [{"id": "o1", "properties": {"trigger": "the review"}}])
    backfill.backfill_domain("journal", targets={"EMOTION": ["trigger"]}, model="m")
    calls = []
    _answer(monkeypatch, [], calls)
    backfill.backfill_domain("journal", targets={"EMOTION": ["trigger"]}, model="m")
    assert calls == []


def test_the_folder_is_stashed_before_it_changes(staged, monkeypatch, tmp_path):
    original = (staged / "observations.json").read_bytes()
    _answer(monkeypatch, [{"id": "o1", "properties": {"trigger": "the review"}}])
    report = backfill.backfill_domain("journal", targets={"EMOTION": ["trigger"]}, model="m")
    (stashed,) = (tmp_path / "logs" / "backfill-stash").rglob("observations.json")
    assert stashed.read_bytes() == original and report["run_id"] in str(stashed)


def test_a_temporal_key_gets_the_anchor_and_redates(staged, monkeypatch):
    (ing.DOMAIN_SCHEMAS_DIR / "journal_schema.yaml").write_text(SCHEMA_V3)
    seen = []
    _answer(monkeypatch, [{"id": "o1", "properties": {"felt_on": "2026-01-06"}}], seen)
    backfill.backfill_domain("journal", targets={"EMOTION": ["felt_on"]}, model="m")
    emotion = _by_class(staged)["EMOTION"]
    assert "document dated 2026-01-07" in seen[0]
    assert (emotion["_valid_from"], emotion["_valid_time_source"]) == ("2026-01-06", "property")


def test_two_classes_share_one_call_per_chunk(staged, monkeypatch):
    seen = []
    _answer(monkeypatch, [], seen)
    backfill.backfill_domain("journal", targets={"EMOTION": ["trigger"], "PERSON": ["role"]}, model="m")
    assert len(seen) == 1 and "For EMOTION" in seen[0] and "For PERSON" in seen[0]
```

`test/test_dry_run_ingest_backfill_properties.py`:

```python
"""`ingest backfill-properties --dryRun`: reads staging only; no LLM, no write (spec 2026-11-15 §4)."""
import json

from click.testing import CliRunner

from _dry_run import assert_effect_shape
from artmind import backfill
from artmind.cli import cli
from test_backfill_properties import staged  # noqa: F401  (fixture)


def _run(*args):
    r = CliRunner().invoke(cli, ["ingest", "backfill-properties", "--domain", "journal", "--entityClass", "EMOTION",
                                 "--property", "trigger", *args, "--compact"])
    assert r.exit_code == 0, r.output
    return json.loads(r.stdout)


def test_dry_run_asks_nothing_writes_nothing_and_matches_the_real_effect(staged, monkeypatch):
    before = {p.name: p.read_bytes() for p in staged.iterdir() if p.is_file()}
    monkeypatch.setattr(backfill, "_ask", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no LLM")))

    dry = _run("--dryRun")

    assert {p.name: p.read_bytes() for p in staged.iterdir() if p.is_file()} == before
    assert dry["dry_run"] is True
    assert_effect_shape(dry["effect"], llm_calls=1)
    assert dry["effect"]["changes"] == [{"kind": "property_backfill", "count": 1, "sample": ["2026-01-07.md"]}]
    assert dry["effect"]["reversible"] is True and "backfill-stash" in dry["effect"]["undo"]
    assert "sent to the configured provider" in dry["effect"]["summary"]

    monkeypatch.setattr(backfill, "_ask", lambda *a, **k: ([{"id": "o1", "properties": {"trigger": "x"}}], True))
    real = _run()
    assert real["dry_run"] is False and real["effect"] == dry["effect"]


def test_nothing_to_fill_is_an_empty_effect(staged, monkeypatch):
    out = _run("--match", "nothing-*", "--dryRun")
    assert out["effect"]["changes"] == [] and out["effect"]["llm"]["calls"] == 0
```

- [ ] **Step 2: Run and confirm failure.** `uv run --group dev pytest test/test_backfill_properties.py test/test_dry_run_ingest_backfill_properties.py -v` — expected: `ImportError` for `artmind.backfill`.

- [ ] **Step 3: Implement** `artmind/backfill.py`:

```python
"""`artmind ingest backfill-properties`: fill declared properties of chosen classes from the staged
chunk text, without re-running entities, relationships, canonicalisation or summaries (spec
2026-11-15 §4).

Each selected chunk's text is sent to the configured provider once (answers cached under
logs/backfill-cache/). Answers merge into the staged observations.json -- only the named keys; names
and ids never change -- valid time is re-derived (retime_observations), each changed folder's
observations.json is stashed under logs/backfill-stash/<run>/ first, then committed with the rebuild
deferred (`commit_to_graph`, which bumps catalogue state), and the projection is rebuilt once."""
from __future__ import annotations

import copy
import fnmatch
import hashlib
import json
import shutil
import uuid
from pathlib import Path

from loguru import logger

from artmind import ingest
from artmind.observations import flatten_domain_props, normalize_property_keys, property_key_rules
from artmind.retime import _load_json, retime_observations, staging_folders


def _ask(step: str, model: str, prompt: str, debug_dir: Path | None = None):
    from artmind.extraction import extract_with_retry
    return extract_with_retry(step, model, prompt, debug_dir)


def scoped_schema(schema: dict, targets: dict[str, list[str]]) -> dict:
    """`schema` cut down to the target classes and keys -- all the prompt renders."""
    types = schema.get("entity_types") or {}
    scoped = {}
    for cls, props in targets.items():
        decl = types.get(cls)
        if not isinstance(decl, dict):
            raise ValueError(f"{cls} is not a class of this schema")
        declared = decl.get("properties") or {}
        missing = [p for p in props if p not in declared]
        if missing:
            raise ValueError(f"{', '.join(missing)} not declared on {cls}; declare it in the schema first")
        scoped[cls] = {**decl, "properties": {p: declared[p] for p in props}}
    out = copy.deepcopy(schema)
    out["entity_types"] = scoped
    return out


def plan(domain: str, targets: dict[str, list[str]], match: str | None = None,
         documents: list[str] | None = None, limit: int | None = None) -> list[dict]:
    """[{folder, document, chunks: [{id, text, observations}]}]: chunks with an observation of a target class."""
    folders = staging_folders(domain)
    if documents:
        wanted = {Path(d).stem for d in documents}
        folders = [f for f in folders if f.name in wanted or f.name.rsplit("~", 1)[0] in wanted]
    if limit is not None:
        folders = folders[:limit]
    out = []
    for folder in folders:
        if folder.name.startswith("table__") or (match and not fnmatch.fnmatch(folder.name, match)):
            continue
        document = _load_json(folder / "document.json", {}) or {}
        if document.get("source_kind") in ("table", ingest.USER_CHAT_SOURCE_KIND):
            continue
        by_chunk: dict[str, list[dict]] = {}
        for o in _load_json(folder / "observations.json", []) or []:
            if o.get("entity_class") in targets and o.get("chunk_id"):
                by_chunk.setdefault(o["chunk_id"], []).append(o)
        texts = {c.get("id"): c.get("text") or "" for c in _load_json(folder / "chunks.json", []) or []}
        chunks = [{"id": cid, "text": texts[cid], "observations": obs}
                  for cid, obs in by_chunk.items() if texts.get(cid, "").strip()]
        if chunks:
            out.append({"folder": folder, "document": document, "chunks": chunks})
    return out


def _prompt(chunk: dict, document: dict, schema: dict, scoped: dict, folder: Path) -> tuple[str, dict]:
    """The properties prompt for this chunk's target observations (canonical names, ids o1..oN), with
    the document's date anchor when a target key is temporal, and the document context block."""
    from artmind import document_context as dc
    from artmind.extraction import build_properties_prompt
    from artmind.temporal import relative_anchor_date

    temporal = any(isinstance(pd, dict) and pd.get("temporal")
                   for decl in scoped["entity_types"].values() for pd in decl["properties"].values())
    anchor = relative_anchor_date(schema, document) if temporal else None
    ids = {f"o{i}": o["id"] for i, o in enumerate(chunk["observations"], 1)}
    entities = [{"id": k, "entity_class": o["entity_class"], "name": o.get("canonical_name") or o.get("name")}
                for k, o in zip(ids, chunk["observations"])]
    text = chunk["text"]
    settings = dc.context_settings(schema)
    if settings.enabled:
        ctx = ingest._read_document_context(folder) or {}
        path = str(document.get("path") or "")
        safe = {**document, "path": Path(path).name if Path(path).is_absolute() else path}  # never a filesystem path
        block = dc.render_context(metadata=dc.build_metadata(safe), outline="", summary=ctx.get("summary") or "",
                                  identifiers=ctx.get("identifiers") or [], cap=settings.cap)
        text = ingest.with_document_context(block, text)
    return build_properties_prompt(text, entities, scoped, anchor_date=anchor), ids


def _cache_path(prompt: str, model: str) -> Path:
    # The prompt holds the chunk text, classes, keys, template and anchor: any change is a new key.
    key = hashlib.sha256(f"{model}\n{prompt}".encode("utf-8")).hexdigest()
    return ingest.LOGS_DIR / "backfill-cache" / f"{key}.json"


def _answer_for(prompt: str, model: str, step: str, debug_dir: Path) -> list | None:
    path = _cache_path(prompt, model)
    cached = _load_json(path)
    if isinstance(cached, list):
        return cached
    answer, ok = _ask(step, model, prompt, debug_dir)
    if not ok:
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(answer, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)
    return answer


def _patch_chunk_cache(folder: Path, changed: list[dict], values: dict[str, dict]) -> None:
    """Best effort (plan question 14): put the filled values into the chunk cache too."""
    if not (folder / "chunks").is_dir():
        return
    for sub in (folder / "chunks").glob("*"):
        for obs in changed:
            seq = str(obs.get("chunk_id") or "").rsplit("_", 1)[-1]
            path = sub / f"chunk_{seq}.json"
            data = _load_json(path)
            if not isinstance(data, dict):
                continue
            ent = next((e for e in data.get("entities") or [] if e.get("name") == obs.get("name")
                        and e.get("entity_class") == obs.get("entity_class")), None)
            if ent is None:
                logger.debug("backfill: no cached entity for {} in {}", obs.get("id"), path.name)
                continue
            entry = next((p for p in data.get("properties") or [] if p.get("id") == ent.get("id")), None)
            if entry is None:
                entry = {"id": ent["id"], "name": ent.get("name"), "properties": {}}
                data.setdefault("properties", []).append(entry)
            entry.setdefault("properties", {}).update(values[obs["id"]])
            path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _effect(domain: str, work: list[dict], targets: dict, model: str) -> dict:
    from artmind.cli_conventions import Change, Effect
    from artmind.effects import topics_touching
    from artmind.topics.shape import estimate_cost

    names = [w["document"].get("name") or w["folder"].name for w in work]
    chunk_chars = [len(c["text"]) for w in work for c in w["chunks"]]
    pairs = ", ".join(f"{c}.{p}" for c, ps in targets.items() for p in ps)
    return Effect(
        summary=(f"Fill {pairs} in {len(work)} staged document(s) of {domain}: {len(chunk_chars)} LLM call(s); "
                 "each chunk's text is sent to the configured provider once. Then commit the changed "
                 "documents and rebuild the projection once."),
        changes=[Change("property_backfill", len(work), names)] if work else [],
        stale_topics=topics_touching(doc_ids=[w["document"]["id"] for w in work if w["document"].get("id")],
                                     doc_names=names),
        llm_calls=len(chunk_chars), llm_model=model if chunk_chars else None,
        est_cost_usd=estimate_cost(chunk_chars, 0) if chunk_chars else None,
        reversible=True,
        undo=(f"copy logs/backfill-stash/<run_id>/{domain}/<folder>/observations.json back into each staging "
              f"folder, then run `artmind ingest retime --domain {domain}`"),
        travels=True, records=[f".artmind/data/kg/{domain}"] if work else [],
    ).to_json()


def backfill_domain(domain: str, *, targets: dict[str, list[str]], model: str, match: str | None = None,
                    dry_run: bool = False, documents: list[str] | None = None, limit: int | None = None,
                    rebuild: bool = True) -> dict:
    from artmind.temporal import load_schema

    schema = load_schema(domain, schemas_dir=ingest.DOMAIN_SCHEMAS_DIR)
    if not schema:
        raise FileNotFoundError(f"Domain schema not found: {ingest.DOMAIN_SCHEMAS_DIR / f'{domain}_schema.yaml'}")
    scoped = scoped_schema(schema, targets)
    work = plan(domain, targets, match, documents, limit)
    effect = _effect(domain, work, targets, model)
    report = {"domain": domain, "dry_run": dry_run, "targets": targets,
              "planned": [w["folder"].name for w in work],
              "chunks": sum(len(w["chunks"]) for w in work), "filled": 0, "committed": 0,
              "commit_failed": [], "failed_chunks": [], "results": [], "run_id": None, "rebuild": None}
    if dry_run:
        report["results"] = [{"folder": w["folder"].name, "document": w["document"].get("name") or w["folder"].name,
                              "before": w["document"].get("_valid_from"), "after": w["document"].get("_valid_from"),
                              "changed": True} for w in work]
        return {**report, "effect": effect}
    run_id = uuid.uuid4().hex[:12]
    aliases, declared_all = property_key_rules(schema)
    debug_dir = ingest.LOGS_DIR / "extraction-debug" / "backfill" / domain
    committed = 0
    for w in work:
        folder, document = w["folder"], w["document"]
        name = document.get("name") or folder.name
        original = _load_json(folder / "observations.json", []) or []
        observations = copy.deepcopy(original)
        by_id = {o["id"]: o for o in observations}
        values: dict[str, dict] = {}
        for chunk in w["chunks"]:
            prompt, ids = _prompt(chunk, document, schema, scoped, folder)
            answer = _answer_for(prompt, model, "backfill_properties", debug_dir)
            if answer is None:
                report["failed_chunks"].append(chunk["id"])
                logger.warning("backfill: {} chunk {} failed; a re-run retries it", name, chunk["id"])
                continue
            for item in answer if isinstance(answer, list) else []:
                oid = ids.get(str((item or {}).get("id")))
                if oid not in by_id:
                    continue
                allowed = set(targets.get(by_id[oid]["entity_class"]) or [])
                props = normalize_property_keys(dict((item or {}).get("properties") or {}), aliases, declared_all)
                props = flatten_domain_props({k: v for k, v in props.items() if k in allowed})
                if props:
                    by_id[oid].update(props)
                    values.setdefault(oid, {}).update(props)
                    report["filled"] += len(props)
        new, _ = retime_observations(observations, document, schema)
        changed = new != original
        row = {"folder": folder.name, "document": name, "before": document.get("_valid_from"), "changed": changed}
        if changed:
            stash = ingest.LOGS_DIR / "backfill-stash" / run_id / domain / folder.name
            stash.mkdir(parents=True, exist_ok=True)
            shutil.copy2(folder / "observations.json", stash / "observations.json")
            ingest.write_staging(folder, {"observations.json": new})
            _patch_chunk_cache(folder, [by_id[i] for i in values], values)
            if ingest.commit_to_graph(folder, domain, defer_rebuild=True):
                committed += 1
            else:
                report["commit_failed"].append(name)
        row["after"] = next((o.get("_valid_from") for o in new if o.get("entity_class") in targets), row["before"])
        report["results"].append(row)
    report.update(run_id=run_id, committed=committed,
                  rebuild=ingest.rebuild_projection(domain) if committed and rebuild else None)
    return {**report, "effect": effect}
```

CLI, in `cli.py` after `ingest_retime`:

```python
@ingest.command("backfill-properties")
@click.option("--domain", required=True, help="Domain whose staged documents to fill")
@click.option("--entityClass", "entity_class", required=True, help="Class whose observations get the properties")
@click.option("--property", "properties", multiple=True, required=True,
              help="Declared property to fill; repeatable, comma-separated")
@click.option("--match", default=None, help="Only staging folders whose name matches this glob")
@dry_run_option("Report documents, chunks and LLM calls; ask nothing, write nothing", legacy=False)
@compact_option
def ingest_backfill_properties(domain, entity_class, properties, match, dry_run, compact) -> None:
    """Fill a class's newly declared properties from the stored chunks.

    \b
    LLM: one call per chunk with an observation of the class; each such chunk's text is sent to the
    configured provider once (cached; a re-run resumes). Entities, names, relationships and summaries
    are not re-run, and only the named keys change. Changed documents are committed and the
    projection is rebuilt once; each changed observations.json is kept under logs/backfill-stash/.
    """
    _setup_logger()
    from artmind import backfill

    env = load_env()
    props = [p.strip() for v in properties for p in v.split(",") if p.strip()]
    try:
        report = backfill.backfill_domain(domain, targets={entity_class: props}, model=resolve_llm_model(env),
                                          match=match, dry_run=dry_run)
    except (FileNotFoundError, ValueError) as e:
        raise click.ClickException(str(e))
    _echo_json(report, compact)
    if report["commit_failed"]:
        raise click.ClickException(f"{len(report['commit_failed'])} document(s) failed to commit -- see the log")
```

Also: add `"ingest backfill-properties"` to tier 1 of `WRITE_COMMANDS`; add `"backfill-properties"` to the "Graph building" list at `cli.py` ~392; check that `artmind.retime` does not import `artmind.backfill` at module level (no cycle).

- [ ] **Step 4: Run and confirm.** `uv run --group dev pytest test/test_backfill_properties.py test/test_dry_run_ingest_backfill_properties.py test/test_cli_conventions.py test/test_cli_guide.py -v`. Expected: PASS. The contract test finds the dry-run test by name.

- [ ] **Step 5: Commit.**

```bash
git add artmind/backfill.py artmind/cli.py artmind/cli_conventions.py test/test_backfill_properties.py test/test_dry_run_ingest_backfill_properties.py
git commit -m "feat(ingest): backfill-properties fills declared keys from staged chunks"
```

**Beyond the suite:**

1. Dry run (safe on any vault):

```bash
cd ~/artmind_vaults/banking_corpus && ARTMIND_NO_PROXY=1 uv run --project $WT artmind ingest backfill-properties --domain banking.risk_governance --entityClass RISK_METRIC --property breach_threshold --dryRun --compact
```

   Expect `effect.llm.calls` equal to the number of chunks with a RISK_METRIC observation, `changes[0].kind == "property_backfill"`, and `git -C ~/artmind_vaults/banking_corpus status --short` empty.

2. Real run: it makes LLM calls and rewrites committed staging and the graph, so **ask the user first**. Then, on one folder:

```bash
cd ~/artmind_vaults/banking_corpus && env -u ANTHROPIC_BASE_URL ARTMIND_NO_PROXY=1 uv run --project $WT artmind ingest backfill-properties --domain banking.risk_governance --entityClass RISK_METRIC --property breach_threshold --match '<one folder>' --compact
```

   Then check `git -C ~/artmind_vaults/banking_corpus diff --stat` shows one `observations.json`, and `query graph pattern2 --shape brief --entityNameList <name>` shows the value.

---

### Task 11: `retime --redoProperties` runs on the backfill

**Files:**
- Modify: `artmind/retime.py` — `retime_domain` redo path becomes one `backfill.backfill_domain` call over every dated class; delete `_reset_properties_step`, `_has_complete_cache`, `_redo_precheck`, `_redo_properties`.
- Modify: `artmind/ingest.py` — `extract_kg`: delete the `canonical_names` and `redo_property_classes` parameters and their branches (lines ~3737–3738, ~3743–3752, ~4136–4142, ~4163–4167); delete `_summarise_document(reuse_any=)` (lines ~2560–2575) and its caller argument (line ~4010). Keep `_build_observations(only_mapped=)` only if `rg` shows another caller.
- Modify: `artmind/cli.py` lines ~1697–1700 — help text.
- Modify tests: `test/test_document_dates.py` (lines ~286–395, ~469–508), `test/test_dry_run_ingest_retime.py` (lines ~56–79), `test/test_document_context_wiring.py` (lines ~204–230), `test/test_document_context_cache.py` (lines ~199–213).

- [ ] **Step 1: Rewrite the redo tests first.** The behaviour they check stays: only `temporal:` classes are asked; canonical names are kept; the anchor is in the prompt; the document is re-dated. The mechanics change:
  - patch `backfill._ask` instead of `ing._llm_extract`; to see which classes were asked, read the prompt's `For <CLASS>, extract:` lines;
  - replace `test_redo_properties_skips_a_document_without_its_chunk_cache` with `test_redo_properties_needs_no_chunk_cache`: delete `folder / "chunks"`, run, and assert `committed == 1`;
  - delete the `reuse_any` and `redo_property_classes` tests in `test_document_context_*`;
  - in `test_dry_run_ingest_retime.py`, the redo dry run still asserts `llm_calls == chunks`, and its "would skip" test becomes "a folder with no dated-class observation is not planned";
  - add a test asserting one call per chunk for a schema with two dated classes.

- [ ] **Step 2: Run and confirm failure.** `uv run --group dev pytest test/test_document_dates.py test/test_dry_run_ingest_retime.py -k redo -v` — expected: FAIL, because retime still calls `extract_kg`.

- [ ] **Step 3: Implement.** In `retime_domain`:
  1. Delete the per-folder `redo_properties` branches. Every folder goes through `retime_folder(folder, schema, write=not dry_run)`.
  2. Before the folder loop:

```python
    redo = None
    if redo_properties:
        from artmind import backfill
        from artmind.temporal import _entity_temporal_mapping

        if not text_model:
            raise ValueError("--redoProperties needs a model")
        targets = {cls: sorted(set(m.values())) for cls, m in _entity_temporal_mapping(schema).items()}
        if targets:
            redo = backfill.backfill_domain(domain, targets=targets, model=text_model, dry_run=dry_run,
                                            documents=documents, limit=limit, rebuild=False)
            committed += redo["committed"]
            failed += redo["commit_failed"]
```

  3. In the loop, a folder retime changes is committed as today; a folder in `redo["planned"]` that retime did not change needs no second commit (the backfill committed it).
  4. `changed_folders` is retime's changed folders plus `redo["planned"]`: the same set in dry and real runs.
  5. `redo_chunks = redo["effect"]["llm"]["calls"] if redo else 0`, passed to `_effect` unchanged.
  6. For each `redo["results"]` row whose folder retime did not change, append it to `results`.
  7. `rebuild_projection(domain)` runs once when `committed > 0`, as today.

  Before deleting the helpers, confirm nothing else uses them:

```bash
rg -n "redo_property_classes|canonical_names=|reuse_any|_reset_properties_step|_has_complete_cache|_redo_precheck|_redo_properties" artmind test
```

- [ ] **Step 4: Run and confirm.** `uv run --group dev pytest test/test_document_dates.py test/test_dry_run_ingest_retime.py test/test_document_context_wiring.py test/test_document_context_cache.py test/test_backfill_properties.py -v && just dev-test`. Expected: PASS.

- [ ] **Step 5: Commit.**

```bash
git add artmind/retime.py artmind/ingest.py artmind/cli.py test/
git commit -m "refactor(retime): --redoProperties runs on backfill-properties; drop the chunk-cache redo path"
```

**Beyond the suite** (no LLM): `cd ~/artmind_vaults/brain01 && ARTMIND_NO_PROXY=1 uv run --project $WT artmind ingest retime --domain personal_journal --redoProperties --limit 3 --dryRun --compact | python3 -c "import json,sys; r=json.load(sys.stdin); print(r['scanned'], r['changed'], r['effect']['llm'])"`. Expect `llm.calls` > 0 and no chunk-cache skips; counts only.

---

## Part 6 — Docs and acceptance

### Task 12: Skills, justfile, CLAUDE.md

**Files:**
- Modify: `artmind/skills/artmind-create-schema/SKILL.md` — add rules: a property must be declared on the class that carries it; `required: true` marks a defining key; `keep_reason:` records evidence the audit cannot measure; guidance and `RIGHT:` examples name only declared keys; alias targets must be declared; run `domains property-audit` and `domains validate`.
- Modify: `artmind/skills/artmind-ingestion-helper/SKILL.md` — add a `backfill-properties` row (when to use it, its dry run, that each chunk's text goes to the provider); note that `retime` drops undeclared keys (kept under `logs/retime-dropped/`) and that `--redoProperties` needs no chunk cache.
- Modify: `artmind/skills/artmind-curate/references/mart.md` — the sequence for a new mart seed property: declare → `ingest backfill-properties` → `mart build`.
- Modify: `artmind/skills/artmind-query/SKILL.md` lines ~346 and ~417–418 — brief entity rows carry `properties` (declared values, required first, capped per row; cuts named in `truncated_properties`); read them before reaching for `--shape full`.
- Modify: `justfile` — add next to `ingest-retime` (line ~300):

```make
# Propose keep/drop/required per property key of a domain (read-only, no LLM)  (usage: just domains-property-audit <domain> [--graph])
domains-property-audit domain *args:
    uv run artmind domains property-audit --domain {{ domain }} {{ args }}

# Fill a class's newly declared properties from staged chunks (LLM; --dryRun first)  (usage: just ingest-backfill-properties <domain> <CLASS> <prop[,prop]> [--dryRun|--match GLOB])
ingest-backfill-properties domain cls props *args:
    uv run artmind ingest backfill-properties --domain {{ domain }} --entityClass {{ cls }} --property {{ props }} {{ args }}
```

- Modify: `CLAUDE.md` — repo-layout rows for `artmind/property_audit.py` and `artmind/backfill.py`; update the `retime.py` row (`--redoProperties` runs on the backfill, needs no chunk cache; undeclared keys dropped and kept under `logs/retime-dropped/`); remove any property-vocabulary mention in the `canonicalize.py` row; add `domains property-audit` and `ingest backfill-properties` to the command notes.

- [ ] **Step 1:** Make the edits above.
- [ ] **Step 2:** `uv run --group dev pytest test/test_cli_conventions.py test/test_cli_guide.py test/test_query_skill_structural_schema.py -v` — expect PASS.
- [ ] **Step 3:** `just dev-cli-help | grep -E "property-audit|backfill-properties"` — both appear.
- [ ] **Step 4: Commit.**

```bash
git add artmind/skills justfile CLAUDE.md
git commit -m "docs: schema-bound properties in skills, justfile and CLAUDE.md"
```

Vault chat agents get the skill edits live through the vault's symlinks. The `~/.artmind` run-folder copies need `cd ~ && artmind setup` after merge, run by the user, never from the checkout.

---

### Task 13: Acceptance runs (spec §8)

These make LLM calls and touch private vault data. Ask the user before each.

- [ ] **Prompt volume.** In banking_corpus first: `cd ~/artmind_vaults/banking_corpus && env -u ANTHROPIC_BASE_URL uv run --project $WT artmind ingest sync <folder> --reextract --match '<glob>'`. Compare properties-step prompt characters in `logs/llm_calls.log` before and after; target a fall of at least 50%. On brain01, a 150-entry sample; figures only.
- [ ] **No undeclared key in staging.** After retime, `domains property-audit` reports 0 rows with `declared: false`.
- [ ] **Property-dated share.** Count observations with `_valid_time_source: property` before and after; must be within 1%.
- [ ] **Answers.** `traces compare --baseline A,D --grade --allowJudge`, 3 runs per side, on both vaults; plus the brain01 mid-property probe set (M03–M05) with brief output. Grades only.
- [ ] **Downstream.** `mart status` coverage unchanged; `insights config check` passes; open conflicts fall (`domains property-audit --graph`).

Record results in the PR body; for brain01, aggregates only.

---

## Per-vault rollout (after merge; the operator runs it)

1. Inside the vault, run `artmind init --force` to update `meta.yaml` and shipped schemas (question 3), or edit a vault-owned schema by hand.
2. Run `artmind domains validate` and fix every property-reference ERROR and WARN. For brain01: declare `location` on EVENT and ACTIVITY; remove the `time_of_visit` alias.
3. Run `artmind domains property-audit --domain d [--graph]`, edit the schema (`required`, `keep_reason`, trims), then `domains validate` again.
4. `artmind ingest retime --domain d --dryRun`, then `artmind ingest retime --domain d`.
5. For each newly declared key: `artmind ingest backfill-properties --domain d --entityClass C --property p --dryRun`, then the same without `--dryRun`.
6. Restart `serve`, `chat-ui` and `admin-ui` from inside the vault.
