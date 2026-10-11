# Query review loop — P3: `review shape` and `review reextract` — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the reviewer two tools.
- **`artmind review shape`** builds the three-way shape-audit bundle for a trace's evidence chunks: the source text as numbered sentences, the extracted subgraph from Neo4j, the staging entries, and the schema sections. It can optionally check an answerability pattern against both the graph and the staging.
- **`artmind review reextract`** re-runs extraction on just those chunks under a proposed schema or prompt template. It writes nothing. It reports a shape diff against the current staging and checks the answerability pattern before and after.

**Architecture:**
- **`artmind/reviews/answerability.py`**: a pure pattern matcher over one normalized subgraph form, `{nodes, edges}`. It has three normalizers: from the graph, from staging, and from a fresh extraction.
- **`artmind/reviews/shape.py`**: locates a chunk's staging folder, reads the chunk's subgraph via `(:Entity)-[:AGGREGATES]->(:Observation)-[:EXTRACTED_FROM]->(:DocChunk)` plus one `RELATES_TO` hop, and builds the bundle.
- **`artmind/reviews/reextract.py`**: composes the pure extraction steps (`build_*_prompt` gains a `meta=` pass-through, then `extract_with_retry`) and computes the shape diff. Canonicalization and the graph-derived vocabularies are skipped. The diff compares against the staging's pre-canonical `name`s, like for like.
- **Data access:** `reextract` needs no Neo4j; `shape` reads the graph read-only.

**Tech Stack:** Python 3.14, click / rich-click, PyYAML, neo4j (read-only), pytest with `CliRunner` and a fake read session.

**Spec:** `docs/superpowers/specs/2026-10-11-query-review-loop-design.md`. P3 covers §7.3, §7.4 and §10's P3 row.

**Branch:** `feat/query-review-p3`, already created from `master` @ 5efe376.

---

## Ground rules for every task

- Run tests from the repo root: `uv run --group dev pytest <path> -v`. The suite is hermetic: `test/conftest.py` redirects `ARTMIND_HOME`/`ARTMIND_DATA_DIR`, drops `ARTMIND_VAULT`, and blocks Neo4j.
- Every new module reads `paths.KG_DIR`, `paths.DOMAIN_SCHEMAS_DIR` and `paths.REVIEWS_DIR` **at call time**, so tests can `monkeypatch.setattr(paths, "KG_DIR", tmp_path / "kg")`.
- Graph reads go through `graph_query.read_session()`, called as a module attribute (`from artmind import graph_query`, then `graph_query.read_session()`), so tests can monkeypatch it.
- Use invented test data only, such as "Mira", "Lakeport" or `doc-1_001`.
- Commit after each task. End every message with a blank line, then exactly:

  ```
  Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
  ```

## File map

| File | Status | Responsibility |
|---|---|---|
| `artmind/extraction.py` | modify | `build_*_prompt(..., meta=None)` forwards `meta` to `prompt_builder.assemble_*` |
| `artmind/reviews/answerability.py` | create | `PatternError`, `parse_pattern`, `load_pattern_arg`, `match`, `from_staging`, `from_extraction` |
| `artmind/reviews/shape.py` | create | `ShapeError`, `OBS_META_KEYS`, `doc_id_of`, `find_staging`, `staging_for_chunk`, `graph_for_chunks`, `sentences`, `default_chunks`, `resolve_target`, `bundle` |
| `artmind/reviews/reextract.py` | create | `extract_chunk`, `current_side`, `proposed_side`, `diff`, `reextract` |
| `artmind/cli.py` | modify | `review shape`, `review reextract` |
| `artmind/reviews/validate.py` | modify | Proof warning text |
| `artmind/skills/artmind-review/SKILL.md`, `references/stage-walk.md`, `references/dossier-format.md` | modify | use the new tools |
| spec, `CLAUDE.md`, `justfile` | modify | docs |
| `test/test_extraction_meta.py`, `test/test_reviews_answerability.py`, `test/test_reviews_shape.py`, `test/test_reviews_reextract.py`, `test/test_reviews_cli.py` | create / modify | tests |

---

### Task 1: `build_*_prompt` takes an optional `meta`

**Files:**
- Modify: `artmind/extraction.py` (`build_entities_prompt`, `build_properties_prompt`, `build_relationships_prompt`)
- Test: `test/test_extraction_meta.py`

- [ ] **Step 1: Write the failing test**

Create `test/test_extraction_meta.py`:

```python
"""The prompt builders accept an alternative meta.yaml dict (for a dry-run
re-extraction under a proposed prompt template)."""

import copy

from artmind import extraction, prompt_builder

SCHEMA = {"name": "d", "description": "A test domain.", "entity_types": {
    "PERSON": {"kind": "recurrent", "description": "A person.", "type_examples": ["friend"]}}}


def _meta_with_marker(step):
    meta = copy.deepcopy(prompt_builder.load_meta())
    meta["prompt_templates"][step] = "MARKER-" + step + "\n" + meta["prompt_templates"][step]
    return meta


def test_entities_prompt_uses_given_meta():
    meta = _meta_with_marker("entities")
    assert extraction.build_entities_prompt("Some text.", SCHEMA, meta=meta).startswith("MARKER-entities")
    assert "MARKER-entities" not in extraction.build_entities_prompt("Some text.", SCHEMA)


def test_properties_and_relationships_prompts_use_given_meta():
    ents = [{"id": "p1", "entity_class": "PERSON", "name": "Mira"}]
    props = extraction.build_properties_prompt("t", ents, SCHEMA, meta=_meta_with_marker("properties"))
    rels = extraction.build_relationships_prompt("t", ents, SCHEMA, meta=_meta_with_marker("relationships"))
    assert props.startswith("MARKER-properties") and "p1 (PERSON): Mira" in props
    assert rels.startswith("MARKER-relationships") and "p1 (PERSON): Mira" in rels


def test_default_meta_output_unchanged():
    ents = [{"id": "p1", "entity_class": "PERSON", "name": "Mira"}]
    assert extraction.build_relationships_prompt("t", ents, SCHEMA) == \
        extraction.build_relationships_prompt("t", ents, SCHEMA, meta=None)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run --group dev pytest test/test_extraction_meta.py -v`
Expected: FAIL with `TypeError: … unexpected keyword argument 'meta'`.

If `prompt_builder.load_meta()`'s templates don't put the template text at the very start of the assembled prompt (for example, a persona line comes first), change the two `startswith("MARKER-…")` assertions to `"MARKER-…" in prompt`. Keep the `not in` assertion for the default case.

- [ ] **Step 3: Implement**

In `artmind/extraction.py`, add a keyword-only `meta: dict | None = None` parameter to each builder and forward it:

```python
def build_entities_prompt(text: str, schema: dict, vocabulary: list | None = None, *, meta: dict | None = None) -> str:
    """`vocabulary` is the retrieved name vocabulary (see
    `artmind.canonicalize.retrieve_vocabulary`) — omitted, the prompt renders
    exactly as before. `meta`: an alternative meta.yaml dict (a proposed
    prompt template, for `artmind review reextract`); None = the run folder's."""
    from artmind.prompt_builder import assemble_entities_prompt

    return assemble_entities_prompt(schema, meta=meta, vocabulary=vocabulary).replace("{text}", text)
```

Do the same for `build_properties_prompt` (`assemble_properties_prompt(schema, meta=meta, vocabulary=vocabulary, anchor_date=anchor_date)`) and `build_relationships_prompt` (`assemble_relationships_prompt(schema, meta=meta)`). Keep every existing positional parameter and docstring line, and add the one-line `meta` note to each docstring.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --group dev pytest test/test_extraction_meta.py test/test_extraction.py test/test_prompt_builder.py -v`
Expected: PASS. If `test_prompt_builder.py` doesn't exist, run `ls test | grep -i prompt` and run whatever prompt tests exist.

- [ ] **Step 5: Commit**

```bash
git add artmind/extraction.py test/test_extraction_meta.py
git commit -m "feat(extraction): prompt builders accept an alternative meta template"
```

---

### Task 2: Answerability patterns — `artmind/reviews/answerability.py`

**Files:**
- Create: `artmind/reviews/answerability.py`
- Test: `test/test_reviews_answerability.py`

**The pattern language** (spec §7.3, extended in Task 6):
- A JSON list in which node steps and relationship steps alternate, starting and ending with a node. Constraint steps (`{"require": …}`) go at the end.
- **Node step:** `{"class": "PERSON", "name": "Mira"}`. Both keys are optional; `{}` matches any node.
- **Relationship step:** `{"rel": "WENT_TO", "dir": "out"}`. `dir` is `out`, `in` or `any`, default `out`. `rel` is optional.
- **Require step:** `{"require": {"node": 0, "properties": ["name"]}}` or `{"require": {"edge": 0, "properties": ["date"]}}`. Node and relationship steps are indexed from 0 in their own sequences. A required property must be present and non-empty, and may be a top-level node field (`name`, `class`).
- **Matching:** `class`, `name` and `rel` compare case-insensitively (casefold equality), so the graph's `WENT_TO` matches staging's `went_to`.

A **subgraph** is `{"nodes": [{"id", "name", "class", "properties": {}}], "edges": [{"source", "target", "rel_type", "properties": {}}]}`.

- [ ] **Step 1: Write the failing tests**

Create `test/test_reviews_answerability.py`:

```python
"""Answerability patterns over a normalized subgraph."""

import json

import pytest

from artmind.reviews import answerability as ans

GRAPH = {
    "nodes": [
        {"id": "d", "name": "Diarist", "class": "PERSON", "properties": {}},
        {"id": "m", "name": "Mira", "class": "PERSON", "properties": {"role": "friend"}},
        {"id": "l", "name": "Lakeport", "class": "LOCATION", "properties": {}},
    ],
    "edges": [
        {"source": "d", "target": "l", "rel_type": "WENT_TO", "properties": {"date": "2031-03-04"}},
        {"source": "m", "target": "d", "rel_type": "SPENDS_TIME_WITH", "properties": {}},
    ],
}


def test_match_simple_path_case_insensitive():
    pat = [{"class": "person", "name": "diarist"}, {"rel": "went_to"}, {"class": "LOCATION"}]
    result = ans.match(pat, GRAPH)
    assert result["matched"] is True
    assert result["matches"] == [["Diarist", "Lakeport"]]


def test_no_match_for_missing_companion_edge():
    pat = [{"class": "PERSON", "name": "Mira"}, {"rel": "WENT_TO"}, {"class": "LOCATION", "name": "Lakeport"}]
    assert ans.match(pat, GRAPH) == {"matched": False, "count": 0, "matches": []}


def test_direction():
    assert ans.match([{"name": "Diarist"}, {"rel": "SPENDS_TIME_WITH", "dir": "in"}, {"name": "Mira"}], GRAPH)["matched"]
    assert not ans.match([{"name": "Diarist"}, {"rel": "SPENDS_TIME_WITH", "dir": "out"}, {"name": "Mira"}], GRAPH)["matched"]
    assert ans.match([{"name": "Diarist"}, {"rel": "SPENDS_TIME_WITH", "dir": "any"}, {"name": "Mira"}], GRAPH)["matched"]


def test_require_node_and_edge_properties():
    base = [{"class": "PERSON"}, {"rel": "WENT_TO"}, {"class": "LOCATION"}]
    assert ans.match(base + [{"require": {"edge": 0, "properties": ["date"]}}], GRAPH)["matched"]
    assert not ans.match(base + [{"require": {"edge": 0, "properties": ["purpose"]}}], GRAPH)["matched"]
    assert ans.match(base + [{"require": {"node": 0, "properties": ["name"]}}], GRAPH)["matched"]
    assert not ans.match(base + [{"require": {"node": 0, "properties": ["role"]}}], GRAPH)["matched"]


def test_single_node_pattern():
    assert ans.match([{"class": "LOCATION", "name": "Lakeport"}], GRAPH)["count"] == 1


@pytest.mark.parametrize("bad", [
    [], {"class": "X"}, [{"rel": "R"}], [{"class": "A"}, {"class": "B"}], [{"class": "A"}, {"rel": "R"}],
    [{"class": "A"}, {"rel": "R", "dir": "up"}, {"class": "B"}],
    [{"class": "A"}, {"require": {"node": 3, "properties": ["x"]}}],
    [{"require": {"node": 0, "properties": ["x"]}}, {"class": "A"}],
])
def test_parse_pattern_rejects(bad):
    with pytest.raises(ans.PatternError):
        ans.parse_pattern(bad)


def test_load_pattern_arg_json_and_file(tmp_path):
    pat = [{"class": "PERSON"}]
    assert ans.load_pattern_arg(json.dumps(pat)) == pat
    f = tmp_path / "p.json"
    f.write_text(json.dumps(pat))
    assert ans.load_pattern_arg(f"@{f}") == pat
    with pytest.raises(ans.PatternError):
        ans.load_pattern_arg("not json")


def test_from_staging_merges_by_name_and_class_and_skips_extracted_from():
    obs = [
        {"name": "Mira", "entity_class": "PERSON", "chunk_id": "c", "role": "friend", "_kind": "recurrent", "id": "o1"},
        {"name": "Lakeport", "entity_class": "LOCATION", "chunk_id": "c", "id": "o2"},
    ]
    rels = [
        {"source_name": "Mira", "target_name": "Lakeport", "rel_type": "went_to", "chunk_id": "c"},
        {"source_name": "Mira", "target_name": "c", "rel_type": "EXTRACTED_FROM", "chunk_id": "c"},
    ]
    g = ans.from_staging(obs, rels)
    assert {(n["name"], n["class"]) for n in g["nodes"]} == {("Mira", "PERSON"), ("Lakeport", "LOCATION")}
    assert next(n for n in g["nodes"] if n["name"] == "Mira")["properties"] == {"role": "friend"}
    assert [e["rel_type"] for e in g["edges"]] == ["went_to"]
    assert ans.match([{"name": "Mira"}, {"rel": "WENT_TO"}, {"name": "Lakeport"}], g)["matched"]


def test_from_extraction():
    ents = [{"id": "p1", "name": "Mira", "entity_class": "PERSON", "properties": {"role": "friend"}},
            {"id": "l1", "name": "Lakeport", "entity_class": "LOCATION", "properties": {}}]
    rels = [{"source_id": "p1", "target_id": "l1", "rel_type": "went_to", "properties": {}},
            {"source_id": "p1", "target_id": "zz", "rel_type": "knows"}]  # dangling id: dropped
    g = ans.from_extraction(ents, rels)
    assert len(g["edges"]) == 1
    assert ans.match([{"name": "Mira"}, {"rel": "went_to"}, {"name": "Lakeport"}], g)["matched"]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --group dev pytest test/test_reviews_answerability.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

Create `artmind/reviews/answerability.py`:

```python
"""Answerability patterns (spec 2026-10-11 §7.3): does a subgraph hold the
traversal a question needs? Pure; no I/O except `load_pattern_arg`'s @file.

A pattern is a JSON list: node and relationship steps alternate, starting and
ending with a node; `require` steps follow.

    [{"class": "PERSON", "name": "Mira"}, {"rel": "WENT_TO", "dir": "out"},
     {"class": "LOCATION"}, {"require": {"edge": 0, "properties": ["date"]}}]

`class`, `name`, `rel` compare case-insensitively; `dir` is out|in|any
(default out); `require` indexes node / relationship steps from 0. A
subgraph is {"nodes": [{id, name, class, properties}], "edges": [{source,
target, rel_type, properties}]}.
"""

import json
from pathlib import Path

MAX_MATCHES = 5
_DIRS = ("out", "in", "any")
_SKIP_RELS = frozenset({"extracted_from"})


class PatternError(ValueError):
    pass


def _cf(value) -> str:
    return str(value or "").strip().casefold()


def parse_pattern(pattern) -> tuple[list[dict], list[dict], list[dict]]:
    """Validate; return (node steps, relationship steps, require steps)."""
    if not isinstance(pattern, list) or not pattern:
        raise PatternError("a pattern is a non-empty JSON list")
    steps, requires = [], []
    for step in pattern:
        if not isinstance(step, dict):
            raise PatternError(f"every step is an object, got {step!r}")
        if "require" in step:
            requires.append(step["require"])
        elif requires:
            raise PatternError("require steps come after every node and relationship step")
        else:
            steps.append(step)
    if not steps or len(steps) % 2 == 0:
        raise PatternError("node and relationship steps alternate, starting and ending with a node")
    nodes, rels = steps[0::2], steps[1::2]
    if any("rel" in n or "dir" in n for n in nodes):
        raise PatternError("a node step was given where a relationship step belongs (or vice versa)")
    if any("class" in r or "name" in r for r in rels):
        raise PatternError("a relationship step has node keys (class/name); steps must alternate")
    for rel in rels:
        if rel.get("dir", "out") not in _DIRS:
            raise PatternError(f"dir must be one of {_DIRS}, got {rel.get('dir')!r}")
    for req in requires:
        if not isinstance(req, dict) or not isinstance(req.get("properties"), list):
            raise PatternError("require is {node|edge: index, properties: [..]}")
        if "node" in req:
            if not isinstance(req["node"], int) or not 0 <= req["node"] < len(nodes):
                raise PatternError(f"require node index {req.get('node')!r} is out of range")
        elif "edge" in req:
            if not isinstance(req["edge"], int) or not 0 <= req["edge"] < len(rels):
                raise PatternError(f"require edge index {req.get('edge')!r} is out of range")
        else:
            raise PatternError("require names a node or an edge index")
    return nodes, rels, requires


def load_pattern_arg(value: str) -> list:
    """A CLI --pattern value: inline JSON, or @path to a JSON file."""
    try:
        text = Path(value[1:]).read_text(encoding="utf-8") if value.startswith("@") else value
        pattern = json.loads(text)
    except (OSError, json.JSONDecodeError) as exc:
        raise PatternError(f"--pattern is not valid JSON (or @file): {exc}") from exc
    parse_pattern(pattern)
    return pattern


def _node_ok(step: dict, node: dict) -> bool:
    if "class" in step and _cf(step["class"]) != _cf(node.get("class")):
        return False
    if "name" in step and _cf(step["name"]) != _cf(node.get("name")):
        return False
    return True


def _filled(value) -> bool:
    return value not in (None, "", [], {})


def _has(item: dict, prop: str) -> bool:
    if prop in ("name", "class", "rel_type") and _filled(item.get(prop)):
        return True
    return _filled((item.get("properties") or {}).get(prop))


def match(pattern, subgraph: dict, *, limit: int = MAX_MATCHES) -> dict:
    node_steps, rel_steps, requires = parse_pattern(pattern)
    nodes = {n["id"]: n for n in subgraph.get("nodes") or []}
    out_edges: dict[str, list[dict]] = {}
    in_edges: dict[str, list[dict]] = {}
    for edge in subgraph.get("edges") or []:
        out_edges.setdefault(edge["source"], []).append(edge)
        in_edges.setdefault(edge["target"], []).append(edge)

    found: list[list[str]] = []
    count = 0

    def satisfied(path_nodes, path_edges) -> bool:
        for req in requires:
            item = path_nodes[req["node"]] if "node" in req else path_edges[req["edge"]]
            if not all(_has(item, p) for p in req["properties"]):
                return False
        return True

    def walk(i: int, path_nodes: list[dict], path_edges: list[dict]) -> None:
        nonlocal count
        if i == len(node_steps):
            if satisfied(path_nodes, path_edges):
                count += 1
                if len(found) < limit:
                    found.append([n.get("name") for n in path_nodes])
            return
        rel = rel_steps[i - 1]
        here = path_nodes[-1]["id"]
        direction = rel.get("dir", "out")
        candidates = []
        if direction in ("out", "any"):
            candidates += [(e, e["target"]) for e in out_edges.get(here, [])]
        if direction in ("in", "any"):
            candidates += [(e, e["source"]) for e in in_edges.get(here, [])]
        for edge, other in candidates:
            if "rel" in rel and _cf(rel["rel"]) != _cf(edge.get("rel_type")):
                continue
            node = nodes.get(other)
            if node is None or not _node_ok(node_steps[i], node) or node in path_nodes:
                continue
            walk(i + 1, path_nodes + [node], path_edges + [edge])

    for node in nodes.values():
        if _node_ok(node_steps[0], node):
            walk(1, [node], [])
    return {"matched": count > 0, "count": count, "matches": found}


_OBS_META = frozenset({
    "id", "name", "canonical_name", "key", "entity_class", "_domain", "doc_id", "doc_version", "chunk_id",
    "_kind", "type", "description", "context", "aliases", "_valid_from", "_valid_to", "_valid_time_source",
    "_doc_valid_from", "_retracts", "embedding",
})


def _staging_node_id(name, cls) -> str:
    return f"{_cf(name)}|{_cf(cls)}"


def from_staging(observations: list[dict], relationships: list[dict]) -> dict:
    """Staging observations/relationships -> subgraph. Nodes merge by
    (name, class); edges join by name (as commit does), EXTRACTED_FROM skipped."""
    nodes: dict[str, dict] = {}
    by_name: dict[str, str] = {}
    for obs in observations:
        nid = _staging_node_id(obs.get("name"), obs.get("entity_class"))
        node = nodes.setdefault(nid, {"id": nid, "name": obs.get("name"), "class": obs.get("entity_class"),
                                      "properties": {}})
        node["properties"].update({k: v for k, v in obs.items() if k not in _OBS_META})
        by_name.setdefault(_cf(obs.get("name")), nid)
        if obs.get("canonical_name"):
            by_name.setdefault(_cf(obs.get("canonical_name")), nid)
    edges = []
    for rel in relationships:
        if _cf(rel.get("rel_type")) in _SKIP_RELS:
            continue
        src, tgt = by_name.get(_cf(rel.get("source_name"))), by_name.get(_cf(rel.get("target_name")))
        if src and tgt:
            edges.append({"source": src, "target": tgt, "rel_type": rel.get("rel_type"),
                          "properties": rel.get("properties") or {}})
    return {"nodes": list(nodes.values()), "edges": edges}


def from_extraction(entities: list[dict], relationships: list[dict]) -> dict:
    """A fresh extraction (LLM ids) -> subgraph. Dangling ids are dropped."""
    nodes = {e["id"]: {"id": e["id"], "name": e.get("name"), "class": e.get("entity_class"),
                       "properties": e.get("properties") or {}} for e in entities if e.get("id")}
    edges = [{"source": r["source_id"], "target": r["target_id"], "rel_type": r.get("rel_type"),
              "properties": r.get("properties") or {}}
             for r in relationships if r.get("source_id") in nodes and r.get("target_id") in nodes]
    return {"nodes": list(nodes.values()), "edges": edges}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --group dev pytest test/test_reviews_answerability.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add artmind/reviews/answerability.py test/test_reviews_answerability.py
git commit -m "feat(reviews): answerability patterns over graph, staging and extraction subgraphs"
```

---

### Task 3: The shape bundle — `artmind/reviews/shape.py`

**Files:**
- Create: `artmind/reviews/shape.py`
- Test: `test/test_reviews_shape.py`

What it does:
- **Staging lookup:** a chunk id is `<doc_id>_<NNN>`. The staging folder is the one under `paths.KG_DIR/<domain>/` whose `document.json` `id` (or `artmind_id`) equals that doc id.
- **Graph read:**

  ```cypher
  MATCH (c:DocChunk) WHERE c.id IN $chunk_ids AND c._domain IN $domains
  MATCH (c)<-[:EXTRACTED_FROM]-(:Observation)<-[:AGGREGATES]-(e:Entity)
  WITH DISTINCT e
  OPTIONAL MATCH (e)-[r:RELATES_TO]-(n:Entity)
  WITH e, collect(DISTINCT CASE WHEN r IS NULL THEN NULL ELSE
       {rel_type: r.rel_type, out: startNode(r) = e, other: n._id, other_name: n.name,
        other_class: n.entity_class, observation_count: r.observation_count} END)[0..$per_entity] AS rels
  RETURN e {.*} AS entity, rels
  ```

- **Default chunks:**
  1. the evidence chunks of productive spans whose document the answer cites;
  2. if none, every productive span's evidence chunks.

  At most 8. The reviewer overrides with `--chunk`.

- [ ] **Step 1: Write the failing tests**

Create `test/test_reviews_shape.py`:

```python
"""The shape-audit bundle: text, graph subgraph, staging, schema."""

import json
import time
from contextlib import contextmanager

import pytest

from artmind.reviews import shape
from artmind.traces import store


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    import artmind.db as db
    import paths

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "reg.db")
    monkeypatch.setattr(paths, "KG_DIR", tmp_path / "kg")
    monkeypatch.setattr(paths, "DOMAIN_SCHEMAS_DIR", tmp_path / "schemas")
    (tmp_path / "schemas").mkdir()
    (tmp_path / "schemas" / "d_schema.yaml").write_text(
        "name: d\nentity_types:\n  PERSON: {kind: recurrent, description: A person.}\n"
        "  LOCATION: {kind: recurrent, description: A place.}\n  EVENT: {kind: occurrent, description: x}\n")
    folder = tmp_path / "kg" / "d" / "2031-03-04"
    folder.mkdir(parents=True)
    (folder / "document.json").write_text(json.dumps({"id": "doc-1", "name": "2031-03-04", "path": "j/2031-03-04.md",
                                                      "_valid_from": "2031-03-04"}))
    (folder / "chunks.json").write_text(json.dumps([{"id": "doc-1_001", "doc_id": "doc-1",
                                                     "text": "I went to Lakeport with Mira. We stayed late!"}]))
    (folder / "observations.json").write_text(json.dumps([
        {"id": "o1", "name": "Mira", "entity_class": "PERSON", "chunk_id": "doc-1_001", "role": "friend"},
        {"id": "o2", "name": "Lakeport", "entity_class": "LOCATION", "chunk_id": "doc-1_001"},
        {"id": "o3", "name": "Other", "entity_class": "PERSON", "chunk_id": "doc-1_002"}]))
    (folder / "relationships.json").write_text(json.dumps([
        {"source_name": "Mira", "target_name": "Lakeport", "rel_type": "visits", "chunk_id": "doc-1_001"},
        {"source_name": "Mira", "target_name": "doc-1_001", "rel_type": "EXTRACTED_FROM", "chunk_id": "doc-1_001"}]))
    (tmp_path / "kg" / "d" / "other").mkdir()
    (tmp_path / "kg" / "d" / "other" / "document.json").write_text(json.dumps({"id": "doc-2"}))


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def data(self):
        return self._rows


@pytest.fixture
def fake_graph(monkeypatch):
    from artmind import graph_query

    calls = []
    rows = [
        {"entity": {"_id": "e-m", "name": "Mira", "entity_class": "PERSON", "role": "friend", "embedding": [0.1]},
         "rels": [{"rel_type": "VISITS", "out": True, "other": "e-l", "other_name": "Lakeport",
                   "other_class": "LOCATION", "observation_count": 1}]},
        {"entity": {"_id": "e-l", "name": "Lakeport", "entity_class": "LOCATION"},
         "rels": [{"rel_type": "VISITS", "out": False, "other": "e-m", "other_name": "Mira",
                   "other_class": "PERSON", "observation_count": 1}]},
    ]

    class _Session:
        def run(self, cypher, **params):
            calls.append((cypher, params))
            return _FakeResult(rows)

    @contextmanager
    def fake_read_session():
        yield _Session()

    monkeypatch.setattr(graph_query, "read_session", fake_read_session)
    monkeypatch.setattr(graph_query, "chunks_by_id", lambda domains, ids, **k: {"rows": [
        {"chunk": {"id": "doc-1_001", "text": "I went to Lakeport with Mira. We stayed late!"},
         "document": {"path": "j/2031-03-04.md", "valid_from": "2031-03-04"}}]})
    return calls


def test_doc_id_of():
    assert shape.doc_id_of("01ab-cd_001") == "01ab-cd"
    assert shape.doc_id_of("plain") == "plain"


def test_staging_for_chunk_filters_by_chunk():
    s = shape.staging_for_chunk("d", "doc-1_001")
    assert s["document"]["id"] == "doc-1"
    assert s["chunk"]["text"].startswith("I went to Lakeport")
    assert [o["name"] for o in s["observations"]] == ["Mira", "Lakeport"]
    assert [r["rel_type"] for r in s["relationships"]] == ["visits"]
    assert shape.staging_for_chunk("d", "doc-9_001") is None


def test_sentences():
    assert shape.sentences("One. Two!\n\nThree?") == [{"n": 1, "text": "One."}, {"n": 2, "text": "Two!"},
                                                     {"n": 3, "text": "Three?"}]


def test_graph_for_chunks_query_and_normalization(fake_graph):
    g = shape.graph_for_chunks(["d"], ["doc-1_001"])
    cypher, params = fake_graph[0]
    assert "EXTRACTED_FROM" in cypher and "AGGREGATES" in cypher and "RELATES_TO" in cypher
    assert params["chunk_ids"] == ["doc-1_001"] and params["domains"] == ["d"]
    names = {n["name"]: n for n in g["nodes"]}
    assert names["Mira"]["properties"] == {"role": "friend"}  # embedding stripped, meta keys dropped
    assert g["edges"] == [{"source": "e-m", "target": "e-l", "rel_type": "VISITS",
                           "properties": {"observation_count": 1}}]


def test_bundle_with_explicit_chunks_and_pattern(fake_graph):
    pattern = [{"name": "Mira"}, {"rel": "WENT_TO"}, {"name": "Lakeport"}]
    b = shape.bundle(domain="d", chunk_ids=["doc-1_001"], pattern=pattern)
    assert b["chunk_ids"] == ["doc-1_001"]
    assert b["text"][0]["sentences"][0] == {"n": 1, "text": "I went to Lakeport with Mira."}
    assert b["staging"]["doc-1_001"]["observations"][0]["name"] == "Mira"
    assert set(b["schema"]["entity_types"]) == {"PERSON", "LOCATION"}  # only classes present
    assert b["answerability"]["graph"]["matched"] is False   # only VISITS, no WENT_TO
    assert b["answerability"]["staging"]["matched"] is False


def _trace_with_cited_chunk():
    store.insert_turn("t1", source="admin", question="Where did Mira go?", started_at=time.time())
    store.update_turn("t1", answer="Lakeport.\n\n### Sources\n- [x](<j/2031-03-04.md>) — d\n", status="done",
                      domains='["d"]', retrieval_path="[]")
    store.insert_span({"span_id": "s1", "trace_id": "t1", "command": "query vector-text", "args": "{}",
                       "exit_code": 0, "duration_ms": 1, "rows": 2, "rows_total": None, "truncated": 0,
                       "evidence": json.dumps({"chunks": [{"chunk_id": "doc-1_001", "doc_id": "doc-1"},
                                                          {"chunk_id": "doc-5_001", "doc_id": "doc-5"}],
                                               "documents": [{"doc_id": "doc-1", "path": "j/2031-03-04.md"},
                                                             {"doc_id": "doc-5", "path": "j/other.md"}]}),
                       "started_at": time.time()}, [])


def test_default_chunks_prefers_cited_documents():
    _trace_with_cited_chunk()
    from artmind.traces.analysis import show

    assert shape.default_chunks(show("t1")) == ["doc-1_001"]


def test_bundle_from_trace(fake_graph):
    _trace_with_cited_chunk()
    b = shape.bundle(trace_id="t1")
    assert b["domain"] == "d" and b["chunk_ids"] == ["doc-1_001"]
    assert b["question"] == "Where did Mira go?"


def test_bundle_errors():
    with pytest.raises(shape.ShapeError):
        shape.bundle(trace_id="missing")
    with pytest.raises(shape.ShapeError):
        shape.bundle(chunk_ids=["doc-1_001"])  # no domain
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --group dev pytest test/test_reviews_shape.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

Create `artmind/reviews/shape.py`:

```python
"""The shape-audit bundle (spec 2026-10-11 §7.3): for a trace's evidence
chunks, the source text as numbered sentences, the subgraph the graph holds
for them (entities extracted from the chunks plus one RELATES_TO hop), the
staging entries extraction produced for them, and the schema sections of the
classes involved. Comparing staging with the graph shows whether a fact was
lost at extraction or at merge/commit. Read-only: graph reads, file reads.
"""

import json
import re
from pathlib import Path

from artmind import graph_query
from artmind.reviews import answerability

MAX_DEFAULT_CHUNKS = 8
PER_ENTITY_EDGES = 50
_CHUNK_SUFFIX = re.compile(r"_\d{3}$")
_SENTENCE_SPLIT = re.compile(r"\n+|(?<=[.!?])\s+")
_ENTITY_META = frozenset({"_id", "name", "entity_class", "label", "key", "_domain", "embedding", "_prop_sources"})

GRAPH_CYPHER = """
MATCH (c:DocChunk) WHERE c.id IN $chunk_ids AND c._domain IN $domains
MATCH (c)<-[:EXTRACTED_FROM]-(:Observation)<-[:AGGREGATES]-(e:Entity)
WITH DISTINCT e
OPTIONAL MATCH (e)-[r:RELATES_TO]-(n:Entity)
WITH e, collect(DISTINCT CASE WHEN r IS NULL THEN NULL ELSE
     {rel_type: r.rel_type, out: startNode(r) = e, other: n._id, other_name: n.name,
      other_class: n.entity_class, observation_count: r.observation_count} END)[0..$per_entity] AS rels
RETURN e {.*} AS entity, rels
"""


class ShapeError(ValueError):
    pass


def doc_id_of(chunk_id: str) -> str:
    return chunk_id.rsplit("_", 1)[0] if _CHUNK_SUFFIX.search(chunk_id) else chunk_id


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def find_staging(domain: str, doc_id: str) -> Path | None:
    import paths

    root = paths.KG_DIR / domain
    if not root.is_dir():
        return None
    for folder in sorted(p for p in root.iterdir() if p.is_dir()):
        doc = _read_json(folder / "document.json", {})
        if doc.get("id") == doc_id or doc.get("artmind_id") == doc_id:
            return folder
    return None


def staging_for_chunk(domain: str, chunk_id: str) -> dict | None:
    folder = find_staging(domain, doc_id_of(chunk_id))
    if folder is None:
        return None
    doc = _read_json(folder / "document.json", {})
    chunk = next((c for c in _read_json(folder / "chunks.json", []) if c.get("id") == chunk_id), None)
    return {
        "folder": str(folder),
        "document": {k: doc.get(k) for k in ("id", "name", "path", "_valid_from", "_valid_to", "_valid_time_source")},
        "chunk": {"id": chunk_id, "text": (chunk or {}).get("text")},
        "observations": [o for o in _read_json(folder / "observations.json", []) if o.get("chunk_id") == chunk_id],
        "relationships": [r for r in _read_json(folder / "relationships.json", [])
                          if r.get("chunk_id") == chunk_id and r.get("rel_type") != "EXTRACTED_FROM"],
    }


def graph_for_chunks(domains: list[str], chunk_ids: list[str]) -> dict:
    with graph_query.read_session() as session:
        rows = session.run(GRAPH_CYPHER, chunk_ids=list(chunk_ids), domains=list(domains),
                           per_entity=PER_ENTITY_EDGES).data()
    nodes: dict[str, dict] = {}
    edges: dict[tuple, dict] = {}
    for row in rows:
        entity = graph_query.strip_internal_props(row["entity"])
        nid = entity.get("_id")
        nodes[nid] = {"id": nid, "name": entity.get("name"), "class": entity.get("entity_class"),
                      "properties": {k: v for k, v in entity.items() if k not in _ENTITY_META},
                      "extracted_here": True}
        for rel in row.get("rels") or []:
            other = rel["other"]
            nodes.setdefault(other, {"id": other, "name": rel.get("other_name"), "class": rel.get("other_class"),
                                     "properties": {}, "extracted_here": False})
            src, tgt = (nid, other) if rel.get("out") else (other, nid)
            edges.setdefault((src, tgt, rel.get("rel_type")),
                             {"source": src, "target": tgt, "rel_type": rel.get("rel_type"),
                              "properties": {"observation_count": rel.get("observation_count")}})
    return {"nodes": list(nodes.values()), "edges": list(edges.values())}


def sentences(text: str | None) -> list[dict]:
    parts = [p.strip() for p in _SENTENCE_SPLIT.split(text or "") if p and p.strip()]
    return [{"n": i, "text": p} for i, p in enumerate(parts, 1)]


def _spans(shown: dict) -> list[dict]:
    return [s for e in shown["events"] for s in e.get("spans", [])] + list(shown.get("unattributed_spans") or [])


def default_chunks(shown: dict) -> list[str]:
    """The answer's cited chunks; else every productive span's evidence chunks."""
    from artmind.traces.path import _same_document, parse_sources

    productive = [s for s in _spans(shown) if s.get("exit_code") == 0 and (s.get("rows") or 0) > 0]
    cited = parse_sources(shown["turn"].get("answer"))
    cited_docs = set()
    for span in productive:
        for doc in (span.get("evidence") or {}).get("documents") or []:
            if doc.get("doc_id") and any(_same_document(c, doc.get("path") or "") for c in cited):
                cited_docs.add(doc["doc_id"])
    every, preferred = [], []
    for span in productive:
        for chunk in (span.get("evidence") or {}).get("chunks") or []:
            cid = chunk.get("chunk_id")
            if cid and cid not in every:
                every.append(cid)
                if chunk.get("doc_id") in cited_docs:
                    preferred.append(cid)
    return (preferred or every)[:MAX_DEFAULT_CHUNKS]


def resolve_target(trace_id: str | None, domain: str | None, chunk_ids) -> tuple[dict | None, str, list[str]]:
    """(the trace as `traces show` returns it, domain, chunk ids) for a review tool."""
    shown = None
    if trace_id:
        from artmind.traces.analysis import show

        shown = show(trace_id)
        if shown is None:
            raise ShapeError(f"no trace {trace_id}")
    if not domain and shown:
        domains = shown["turn"].get("domains") or []
        domain = domains[0] if len(domains) == 1 else None
        if domain is None and len(domains) > 1:
            raise ShapeError(f"the trace spans several domains ({', '.join(domains)}); pass --domain")
    if not domain:
        raise ShapeError("pass --domain (or a trace whose spans name one domain)")
    chunk_ids = list(chunk_ids) or (default_chunks(shown) if shown else [])
    if not chunk_ids:
        raise ShapeError("no evidence chunks found; pass --chunk")
    return shown, domain, chunk_ids


def bundle(*, trace_id: str | None = None, domain: str | None = None, chunk_ids=(), pattern=None) -> dict:
    import paths
    from artmind.temporal import load_schema

    shown, domain, chunk_ids = resolve_target(trace_id, domain, chunk_ids)
    staging = {cid: staging_for_chunk(domain, cid) for cid in chunk_ids}
    rows = {r["chunk"]["id"]: r for r in graph_query.chunks_by_id([domain], chunk_ids).get("rows") or []}
    text = []
    for cid in chunk_ids:
        row = rows.get(cid) or {}
        body = (row.get("chunk") or {}).get("text") or ((staging.get(cid) or {}).get("chunk") or {}).get("text")
        text.append({"chunk_id": cid, "document": (row.get("document") or {}).get("path"),
                     "sentences": sentences(body)})
    graph = graph_for_chunks([domain], chunk_ids)
    observations = [o for s in staging.values() if s for o in s["observations"]]
    relationships = [r for s in staging.values() if s for r in s["relationships"]]
    schema = load_schema(domain, schemas_dir=paths.DOMAIN_SCHEMAS_DIR)
    classes = {n.get("class") for n in graph["nodes"]} | {o.get("entity_class") for o in observations}
    types = schema.get("entity_types") or {}
    result = {
        "trace_id": trace_id,
        "domain": domain,
        "chunk_ids": chunk_ids,
        "question": shown["turn"]["question"] if shown else None,
        "expected": (shown.get("feedback") or {}).get("expected") if shown else None,
        "answer_basis": shown["turn"].get("answer_basis") if shown else None,
        "text": text,
        "graph": graph,
        "staging": staging,
        "schema": {"guidance": schema.get("guidance"),
                   "entity_types": {c: types[c] for c in sorted(c for c in classes if c in types)}},
    }
    if pattern is not None:
        result["answerability"] = {
            "graph": answerability.match(pattern, graph),
            "staging": answerability.match(pattern, answerability.from_staging(observations, relationships)),
        }
    return result
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --group dev pytest test/test_reviews_shape.py -v`
Expected: PASS.

`load_schema` might not accept `schemas_dir`, or might be named differently. Check `artmind/temporal.py:225` and adapt the call only. The graph test's normalized edge list depends on dedupe: both rows describe the same `e-m → e-l` edge, so exactly one edge is expected.

- [ ] **Step 5: Commit**

```bash
git add artmind/reviews/shape.py test/test_reviews_shape.py
git commit -m "feat(reviews): shape bundle -- text, graph subgraph, staging, schema for evidence chunks"
```

---

### Task 4: Dry-run re-extraction — `artmind/reviews/reextract.py`

**Files:**
- Create: `artmind/reviews/reextract.py`
- Test: `test/test_reviews_reextract.py`

**What it compares.** For each chunk (at most `max_chunks`):
- **Current side:** the staging observations and relationships (pre-canonical `name`s; `EXTRACTED_FROM` excluded).
- **Proposed side:** the entities, properties and relationships steps run on the chunk text under the proposed schema and/or meta. No name vocabulary, no property vocabulary, no canonicalization.

Nodes are keyed by `(name, class)` and edges by `(source name, rel type, target name)`, all casefolded. Properties are compared for nodes present on both sides. **Nothing is written** anywhere except the LLM audit log that every `call_llm` appends to.

- [ ] **Step 1: Write the failing tests**

Create `test/test_reviews_reextract.py`:

```python
"""Dry-run re-extraction: proposed schema/meta vs current staging, nothing written."""

import json
import os

import pytest

from artmind.reviews import reextract


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    import artmind.db as db
    import paths

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "reg.db")
    monkeypatch.setattr(paths, "KG_DIR", tmp_path / "kg")
    monkeypatch.setattr(paths, "DOMAIN_SCHEMAS_DIR", tmp_path / "schemas")
    (tmp_path / "schemas").mkdir()
    (tmp_path / "schemas" / "d_schema.yaml").write_text(
        "name: d\nentity_types:\n  PERSON: {kind: recurrent, description: A person.}\n"
        "  LOCATION: {kind: recurrent, description: A place.}\n")
    folder = tmp_path / "kg" / "d" / "2031-03-04"
    folder.mkdir(parents=True)
    (folder / "document.json").write_text(json.dumps({"id": "doc-1", "_valid_from": "2031-03-04"}))
    (folder / "chunks.json").write_text(json.dumps([{"id": "doc-1_001", "text": "I went to Lakeport with Mira."}]))
    (folder / "observations.json").write_text(json.dumps([
        {"name": "Arlo Venn", "entity_class": "PERSON", "chunk_id": "doc-1_001", "role": "groom"},
        {"name": "Mira", "entity_class": "PERSON", "chunk_id": "doc-1_001"},
        {"name": "Lakeport", "entity_class": "LOCATION", "chunk_id": "doc-1_001"}]))
    (folder / "relationships.json").write_text(json.dumps([
        {"source_name": "Arlo Venn", "target_name": "Lakeport", "rel_type": "went_to", "chunk_id": "doc-1_001"}]))
    proposed = tmp_path / "proposed_schema.yaml"
    proposed.write_text((tmp_path / "schemas" / "d_schema.yaml").read_text()
                        + "guidance:\n  entities: NARRATOR-RULE \"I\" is the Diarist.\n")
    return folder


ENTITIES = [{"id": "p1", "name": "Diarist", "entity_class": "PERSON"},
            {"id": "p2", "name": "Mira", "entity_class": "PERSON"},
            {"id": "l1", "name": "Lakeport", "entity_class": "LOCATION"}]
PROPS = [{"id": "p2", "properties": {"role": "friend"}}]
RELS = [{"source_id": "p1", "target_id": "l1", "rel_type": "went_to"},
        {"source_id": "p2", "target_id": "l1", "rel_type": "went_to"}]


@pytest.fixture
def fake_llm(monkeypatch):
    from artmind import extraction

    prompts = []

    def fake(step_name, model, prompt, debug_dir=None):
        prompts.append((step_name, model, prompt))
        if step_name.endswith("entities"):
            return [dict(e) for e in ENTITIES], True
        if step_name.endswith("properties"):
            return PROPS, True
        return RELS, True

    monkeypatch.setattr(extraction, "extract_with_retry", fake)
    return prompts


def _snapshot(root):
    return {p: (p.stat().st_mtime_ns, p.read_bytes()) for p in root.rglob("*") if p.is_file()}


def test_extract_chunk_merges_properties(fake_llm):
    out = reextract.extract_chunk("text", {"name": "d", "entity_types": {}}, model="m")
    assert out["failed_steps"] == []
    assert next(e for e in out["entities"] if e["id"] == "p2")["properties"] == {"role": "friend"}
    assert [p[0] for p in fake_llm] == ["reextract_entities", "reextract_properties", "reextract_relationships"]


def test_diff():
    current = reextract.current_side(
        [{"name": "Arlo Venn", "entity_class": "PERSON", "role": "groom"}, {"name": "Lakeport", "entity_class": "LOCATION"}],
        [{"source_name": "Arlo Venn", "target_name": "Lakeport", "rel_type": "went_to"}])
    proposed = reextract.proposed_side(
        [{"id": "a", "name": "arlo venn", "entity_class": "PERSON", "properties": {"role": "guest"}},
         {"id": "l", "name": "Lakeport", "entity_class": "LOCATION", "properties": {}},
         {"id": "d", "name": "Diarist", "entity_class": "PERSON", "properties": {}}],
        [{"source_id": "d", "target_id": "l", "rel_type": "WENT_TO"}])
    d = reextract.diff(current, proposed)
    assert d["nodes"] == {"added": ["Diarist (PERSON)"], "removed": [], "kept": 2}
    assert d["edges"] == {"added": ["diarist -went_to-> lakeport"], "removed": ["arlo venn -went_to-> lakeport"], "kept": 0}
    assert d["properties"] == [{"node": "arlo venn (PERSON)", "added": {}, "removed": {}, "changed": {"role": ["groom", "guest"]}}]


def test_reextract_uses_proposed_schema_writes_nothing_and_checks_pattern(tmp_path, fake_llm, _isolated):
    before = _snapshot(tmp_path / "kg")
    pattern = [{"name": "Mira"}, {"rel": "WENT_TO"}, {"name": "Lakeport"}]
    result = reextract.reextract(domain="d", chunk_ids=["doc-1_001"], schema_path=tmp_path / "proposed_schema.yaml",
                                 pattern=pattern, model="test-model")
    assert _snapshot(tmp_path / "kg") == before
    assert not (tmp_path / "reg.db").exists() or os.path.getsize(tmp_path / "reg.db") >= 0
    assert result["writes"] == "none" and result["model"] == "test-model"
    assert "NARRATOR-RULE" in fake_llm[0][2]             # the proposed schema reached the entities prompt
    chunk = result["chunks"][0]
    assert chunk["chunk_id"] == "doc-1_001"
    assert "Diarist (PERSON)" in chunk["diff"]["nodes"]["added"]
    assert "Arlo Venn (PERSON)" in chunk["diff"]["nodes"]["removed"]
    assert "mira -went_to-> lakeport" in chunk["diff"]["edges"]["added"]
    assert chunk["answerability"] == {"current": {"matched": False, "count": 0, "matches": []},
                                      "proposed": {"matched": True, "count": 1, "matches": [["Mira", "Lakeport"]]}}


def test_reextract_baseline_and_missing_staging(tmp_path, fake_llm):
    result = reextract.reextract(domain="d", chunk_ids=["doc-1_001", "doc-9_001"], model="m")
    assert result["schema"] == "(current)" and result["meta"] == "(current)"
    assert result["chunks"][1] == {"chunk_id": "doc-9_001", "error": "no staging folder holds this chunk"}
    assert "NARRATOR-RULE" not in fake_llm[0][2]


def test_reextract_respects_max_chunks(fake_llm):
    result = reextract.reextract(domain="d", chunk_ids=["doc-1_001", "doc-1_001b", "doc-1_001c"], max_chunks=1, model="m")
    assert [c["chunk_id"] for c in result["chunks"]] == ["doc-1_001"]
    assert result["llm_calls_planned"] == 3


def test_bad_schema_file(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("- not a mapping\n")
    with pytest.raises(reextract.ReextractError):
        reextract.reextract(domain="d", chunk_ids=["doc-1_001"], schema_path=bad, model="m")
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --group dev pytest test/test_reviews_reextract.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

Create `artmind/reviews/reextract.py`:

```python
"""Dry-run re-extraction (spec 2026-10-11 §7.4): re-run the entities,
properties and relationships prompts on a few evidence chunks under a
proposed schema and/or meta.yaml, and diff the result against the chunks'
current staging. Writes nothing (no staging, graph, registry); every LLM call
is appended to the LLM audit log as usual.

Like-for-like: the proposed side skips the graph-derived name/property
vocabularies and the per-document canonicalization pass, so it is compared
with staging's pre-canonical `name`s. Run once with neither --schema nor
--meta to see how much the diff moves from LLM variance alone.
"""

import json
from pathlib import Path

import yaml

from artmind.reviews import answerability, shape

STEP_PREFIX = "reextract"
_OBS_META = answerability._OBS_META


class ReextractError(ValueError):
    pass


def _cf(value) -> str:
    return str(value or "").strip().casefold()


def extract_chunk(text: str, schema: dict, *, model: str, meta: dict | None = None,
                  anchor_date: str | None = None) -> dict:
    from artmind import extraction

    failed: list[str] = []
    raw, ok = extraction.extract_with_retry(f"{STEP_PREFIX}_entities", model,
                                            extraction.build_entities_prompt(text, schema, meta=meta))
    if not ok:
        failed.append("entities")
    entities = [dict(e) for e in raw or [] if isinstance(e, dict) and e.get("id") and e.get("name")]
    for entity in entities:
        entity.setdefault("entity_class", entity.get("type") or "")
    relationships: list[dict] = []
    props: dict[str, dict] = {}
    if entities:
        raw_props, ok = extraction.extract_with_retry(
            f"{STEP_PREFIX}_properties", model,
            extraction.build_properties_prompt(text, entities, schema, anchor_date=anchor_date, meta=meta))
        if not ok:
            failed.append("properties")
        for item in raw_props or []:
            if isinstance(item, dict) and item.get("id"):
                props[item["id"]] = item.get("properties") or {}
        raw_rels, ok = extraction.extract_with_retry(
            f"{STEP_PREFIX}_relationships", model,
            extraction.build_relationships_prompt(text, entities, schema, meta=meta))
        if not ok:
            failed.append("relationships")
        relationships = [r for r in raw_rels or []
                         if isinstance(r, dict) and r.get("source_id") and r.get("target_id") and r.get("rel_type")]
    for entity in entities:
        entity["properties"] = props.get(entity["id"], {})
    return {"entities": entities, "relationships": relationships, "failed_steps": failed}


def _node_label(name, cls) -> str:
    return f"{str(name).strip()} ({cls})"


def current_side(observations: list[dict], relationships: list[dict]) -> dict:
    nodes = {}
    for obs in observations:
        key = (_cf(obs.get("name")), _cf(obs.get("entity_class")))
        entry = nodes.setdefault(key, {"label": _node_label(obs.get("name"), obs.get("entity_class")), "props": {}})
        entry["props"].update({k: v for k, v in obs.items() if k not in _OBS_META})
    edges = {(_cf(r.get("source_name")), _cf(r.get("rel_type")), _cf(r.get("target_name")))
             for r in relationships if _cf(r.get("rel_type")) != "extracted_from"}
    return {"nodes": nodes, "edges": edges}


def proposed_side(entities: list[dict], relationships: list[dict]) -> dict:
    nodes, names = {}, {}
    for entity in entities:
        key = (_cf(entity.get("name")), _cf(entity.get("entity_class")))
        nodes.setdefault(key, {"label": _node_label(entity.get("name"), entity.get("entity_class")), "props": {}})
        nodes[key]["props"].update(entity.get("properties") or {})
        names[entity.get("id")] = _cf(entity.get("name"))
    edges = {(names[r["source_id"]], _cf(r.get("rel_type")), names[r["target_id"]])
             for r in relationships if r.get("source_id") in names and r.get("target_id") in names}
    return {"nodes": nodes, "edges": edges}


def _edge_label(edge: tuple) -> str:
    return f"{edge[0]} -{edge[1]}-> {edge[2]}"


def diff(current: dict, proposed: dict) -> dict:
    cur_nodes, new_nodes = current["nodes"], proposed["nodes"]
    kept = sorted(set(cur_nodes) & set(new_nodes))
    properties = []
    for key in kept:
        old, new = cur_nodes[key]["props"], new_nodes[key]["props"]
        added = {k: v for k, v in new.items() if k not in old}
        removed = {k: v for k, v in old.items() if k not in new}
        changed = {k: [old[k], new[k]] for k in old.keys() & new.keys()
                   if json.dumps(old[k], sort_keys=True, default=str) != json.dumps(new[k], sort_keys=True, default=str)}
        if added or removed or changed:
            properties.append({"node": f"{key[0]} ({cur_nodes[key]['label'].rsplit('(', 1)[1][:-1]})",
                               "added": added, "removed": removed, "changed": changed})
    return {
        "nodes": {"added": sorted(new_nodes[k]["label"] for k in set(new_nodes) - set(cur_nodes)),
                  "removed": sorted(cur_nodes[k]["label"] for k in set(cur_nodes) - set(new_nodes)),
                  "kept": len(kept)},
        "edges": {"added": sorted(_edge_label(e) for e in proposed["edges"] - current["edges"]),
                  "removed": sorted(_edge_label(e) for e in current["edges"] - proposed["edges"]),
                  "kept": len(current["edges"] & proposed["edges"])},
        "properties": properties,
    }


def _load_yaml(path: Path | str, what: str) -> dict:
    try:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ReextractError(f"cannot read the {what} file {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ReextractError(f"the {what} file {path} must hold a YAML mapping")
    return data


def reextract(*, trace_id: str | None = None, domain: str | None = None, chunk_ids=(),
              schema_path=None, meta_path=None, max_chunks: int = 5, pattern=None, model: str | None = None) -> dict:
    import paths
    from artmind.temporal import load_schema, relative_anchor_date
    from utils.functions import load_env, resolve_llm_model

    try:
        _, domain, chunk_ids = shape.resolve_target(trace_id, domain, chunk_ids)
    except shape.ShapeError as exc:
        raise ReextractError(str(exc)) from exc
    chunk_ids = chunk_ids[:max_chunks]
    schema = (_load_yaml(schema_path, "schema") if schema_path
              else load_schema(domain, schemas_dir=paths.DOMAIN_SCHEMAS_DIR))
    meta = _load_yaml(meta_path, "meta") if meta_path else None
    if meta is not None and not isinstance(meta.get("prompt_templates"), dict):
        raise ReextractError("the meta file must define prompt_templates (copy domains/meta.yaml and edit it)")
    model = resolve_llm_model(load_env(), override=model)

    chunks = []
    for cid in chunk_ids:
        staged = shape.staging_for_chunk(domain, cid)
        if staged is None or not staged["chunk"].get("text"):
            chunks.append({"chunk_id": cid, "error": "no staging folder holds this chunk"})
            continue
        doc = staged["document"]
        anchor = relative_anchor_date(schema, {k: doc.get(k) for k in ("_valid_from", "_valid_to", "_valid_time_source")})
        proposed = extract_chunk(staged["chunk"]["text"], schema, model=model, meta=meta, anchor_date=anchor)
        item = {
            "chunk_id": cid,
            "document": doc.get("path") or doc.get("name"),
            "failed_steps": proposed["failed_steps"],
            "diff": diff(current_side(staged["observations"], staged["relationships"]),
                         proposed_side(proposed["entities"], proposed["relationships"])),
        }
        if pattern is not None:
            item["answerability"] = {
                "current": answerability.match(pattern, answerability.from_staging(staged["observations"],
                                                                                   staged["relationships"])),
                "proposed": answerability.match(pattern, answerability.from_extraction(proposed["entities"],
                                                                                       proposed["relationships"])),
            }
        chunks.append(item)
    return {
        "trace_id": trace_id,
        "domain": domain,
        "model": model,
        "schema": str(schema_path) if schema_path else "(current)",
        "meta": str(meta_path) if meta_path else "(current)",
        "llm_calls_planned": 3 * len(chunk_ids),
        "writes": "none",
        "chunks": chunks,
    }
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --group dev pytest test/test_reviews_reextract.py -v`
Expected: PASS.

`diff` reports a kept node with properties as `"<casefolded name> (<class>)"`. The test expects `"arlo venn (PERSON)"`. If the label slicing is awkward, build that string directly from `key[0]` and the original class, and keep the test's expected value.

`test_reextract_uses_proposed_schema…` looks for the proposed schema's `guidance.entities` text in the entities prompt. `prompt_builder` renders `schema["guidance"]["entities"]` under "DOMAIN-SPECIFIC GUIDANCE:". If the text doesn't appear, read `assemble_entities_prompt`. Don't weaken the assertion.

- [ ] **Step 5: Commit**

```bash
git add artmind/reviews/reextract.py test/test_reviews_reextract.py
git commit -m "feat(reviews): dry-run re-extraction with shape diff and answerability"
```

---

### Task 5: CLI — `review shape`, `review reextract`

**Files:**
- Modify: `artmind/cli.py` (add after the `review leakcheck` command)
- Test: `test/test_reviews_cli.py` (append)

- [ ] **Step 1: Write the failing tests** (append to `test/test_reviews_cli.py`)

```python
def test_shape_cli(monkeypatch):
    from artmind.reviews import shape

    seen = {}

    def fake_bundle(**kwargs):
        seen.update(kwargs)
        return {"domain": kwargs["domain"], "chunk_ids": kwargs["chunk_ids"]}

    monkeypatch.setattr(shape, "bundle", fake_bundle)
    out = json.loads(_run("shape", "--domain", "d", "--chunk", "c-1,c-2", "--pattern", '[{"class": "PERSON"}]',
                          "--compact").stdout)
    assert out == {"domain": "d", "chunk_ids": ["c-1", "c-2"]}
    assert seen["pattern"] == [{"class": "PERSON"}] and seen["trace_id"] is None


def test_shape_cli_errors(monkeypatch):
    result = _run("shape", "--domain", "d", "--pattern", "nope", ok=False)
    assert result.exit_code != 0 and "pattern" in result.output.lower()
    result = _run("shape", ok=False)
    assert result.exit_code != 0


def test_reextract_cli(monkeypatch, tmp_path):
    from artmind.reviews import reextract

    seen = {}
    monkeypatch.setattr(reextract, "reextract", lambda **kw: seen.update(kw) or {"writes": "none", "chunks": []})
    schema = tmp_path / "s.yaml"
    schema.write_text("name: d\n")
    out = json.loads(_run("reextract", "t1", "--schema", str(schema), "--maxChunks", "2", "--model", "m",
                          "--compact").stdout)
    assert out["writes"] == "none"
    assert seen["trace_id"] == "t1" and seen["max_chunks"] == 2 and seen["model"] == "m"
    assert str(seen["schema_path"]) == str(schema) and seen["meta_path"] is None
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --group dev pytest test/test_reviews_cli.py -v`
Expected: the new tests FAIL with `No such command 'shape'`.

- [ ] **Step 3: Implement** (in `artmind/cli.py`, after `review_leakcheck`)

```python
@review_group.command("shape")
@click.argument("trace_id", required=False)
@click.option("--chunk", "chunks", multiple=True, help="Evidence chunk id (repeatable; comma-splittable); default: the chunks the answer cited")
@click.option("--domain", default=None, help="Domain (default: the trace's)")
@click.option("--pattern", default=None, help="Answerability pattern: JSON, or @path to a JSON file")
@click.option("--compact", is_flag=True, help="Emit compact JSON")
def review_shape(trace_id, chunks, domain, pattern, compact) -> None:
    """The shape-audit bundle for a trace's evidence chunks: numbered source sentences,
    the graph subgraph extracted from them (+1 hop), their staging entries, the schema
    sections involved, and optionally an answerability check against graph and staging."""
    from artmind.reviews import answerability, shape

    try:
        parsed = answerability.load_pattern_arg(pattern) if pattern else None
        result = shape.bundle(trace_id=trace_id, domain=domain, chunk_ids=_parse_repeatable(chunks), pattern=parsed)
    except (shape.ShapeError, answerability.PatternError) as exc:
        raise click.ClickException(str(exc)) from exc
    _echo_json(result, compact)


@review_group.command("reextract")
@click.argument("trace_id", required=False)
@click.option("--chunk", "chunks", multiple=True, help="Evidence chunk id (repeatable; comma-splittable); default: the chunks the answer cited")
@click.option("--domain", default=None, help="Domain (default: the trace's)")
@click.option("--schema", "schema_path", default=None, type=click.Path(dir_okay=False, exists=True),
              help="Proposed schema YAML (a full schema file); default: the current schema")
@click.option("--meta", "meta_path", default=None, type=click.Path(dir_okay=False, exists=True),
              help="Proposed meta.yaml (prompt templates); default: the current one")
@click.option("--maxChunks", "max_chunks", type=int, default=5, show_default=True, help="At most this many chunks (3 LLM calls each)")
@click.option("--pattern", default=None, help="Answerability pattern: JSON, or @path to a JSON file")
@click.option("--model", default=None, help="Extraction model (default: ARTMIND_KG_LLM_MODEL)")
@click.option("--compact", is_flag=True, help="Emit compact JSON")
def review_reextract(trace_id, chunks, domain, schema_path, meta_path, max_chunks, pattern, model, compact) -> None:
    """Dry-run re-extraction of evidence chunks under a proposed schema/prompt: a shape
    diff against current staging, plus answerability before and after. Writes nothing.
    Run once without --schema/--meta to see how much LLM variance alone moves the diff."""
    from artmind.reviews import answerability, reextract

    try:
        parsed = answerability.load_pattern_arg(pattern) if pattern else None
        result = reextract.reextract(trace_id=trace_id, domain=domain, chunk_ids=_parse_repeatable(chunks),
                                     schema_path=schema_path, meta_path=meta_path, max_chunks=max_chunks,
                                     pattern=parsed, model=model)
    except (reextract.ReextractError, answerability.PatternError) as exc:
        raise click.ClickException(str(exc)) from exc
    _echo_json(result, compact)
```

Update the `review_group` docstring with one more sentence: "`shape` and `reextract` support the shape audit and prove a schema or prompt fix before any re-ingest."

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --group dev pytest test/test_reviews_cli.py test/test_cli_guide.py -v`, then `uv run artmind review --help`.
Expected: PASS, with nine commands listed.

- [ ] **Step 5: Commit**

```bash
git add artmind/cli.py test/test_reviews_cli.py
git commit -m "feat(reviews): artmind review shape and review reextract"
```

---

### Task 6: Skill, validation text, docs, spec

**Files:**
- Modify: `artmind/skills/artmind-review/SKILL.md`, `references/stage-walk.md`, `references/dossier-format.md`
- Modify: `artmind/reviews/validate.py` (Proof warning text)
- Modify: `CLAUDE.md`, `justfile`, the spec
- Test: `test/test_reviews_skill.py` (append), `test/test_reviews_validate.py` (the warnings test still passes)

- [ ] **Step 1: Write the failing test** (append to `test/test_reviews_skill.py`)

```python
def test_skill_uses_the_shape_tools():
    walk = (SKILL / "references" / "stage-walk.md").read_text()
    skill = (SKILL / "SKILL.md").read_text()
    assert "artmind review shape" in walk and "artmind review reextract" in walk
    assert "later phase" not in skill
    assert "outside the checkout" in walk  # proposed schema scratch files stay out of the public repo
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run --group dev pytest test/test_reviews_skill.py -v`
Expected: FAIL.

- [ ] **Step 3: Update the skill**

**`SKILL.md`:**
- In the Commands table, add these two rows after `review validate`:

  ```markdown
  | `artmind review shape <trace_id> [--chunk …] [--pattern '<json>']` | the shape-audit bundle: numbered sentences, graph subgraph, staging, schema, answerability |
  | `artmind review reextract <trace_id> --schema <proposed.yaml> [--pattern …]` | dry-run re-extraction under a proposed schema (or `--meta`): shape diff + answerability; writes nothing |
  ```

- Delete the paragraph that starts "`review shape` and `review reextract` (automatic shape bundles…".
- In Flow step 6, change "the checklist and the staging lookup are in" to "run `artmind review shape <trace_id>`; the checklist is in".
- In Flow step 7, change "Proof (for a view: run `artmind views test`)" to "Proof (a view: `artmind views test`; a schema or prompt change: `artmind review reextract`)".

**`references/stage-walk.md`:** replace everything from `## Shape audit` up to (not including) the checklist line `Grade against the checklist; …` with:

````markdown
## Shape audit

Build the bundle — text, graph, staging, schema for the ground-truth chunks:

```bash
artmind review shape <trace_id>                      # chunks the answer cited
artmind review shape <trace_id> --chunk <id> --chunk <id>   # the chunks a correct answer needs
```

- `text` — each chunk's sentences, numbered (quote them as `>` in the dossier).
- `graph` — the entities extracted from those chunks plus one `RELATES_TO` hop.
- `staging` — what extraction produced for each chunk before merge/commit. In
  staging but not in the graph → lost at merge/commit (identity, or a code bug).
  Not in staging → lost at extraction (or the schema has no place for it).
- `schema` — the `guidance` and the classes involved.

Write the **answerability pattern** — the traversal that would answer the
question — and pass it with `--pattern`; the bundle reports whether the graph
and the staging match it:

```json
[{"class": "PERSON", "name": "<who>"}, {"rel": "WENT_TO", "dir": "out"}, {"class": "LOCATION"},
 {"require": {"edge": 0, "properties": ["date"]}}]
```

Steps alternate node / relationship (`dir`: out, in, any); `require` steps go
last and index nodes or edges from 0. Names, classes and relationship types
compare case-insensitively.

## Proving a schema or prompt fix

Write the proposed schema (a full copy of the domain's schema YAML, edited) or
a proposed `meta.yaml` to a scratch file **outside the checkout** (e.g. under
`$TMPDIR`) — it is derived from vault content. Then:

```bash
artmind review reextract <trace_id> --pattern '<json>'                          # baseline: LLM variance
artmind review reextract <trace_id> --schema "$TMPDIR/proposed.yaml" --pattern '<json>'
```

It re-extracts at most `--maxChunks` chunks (3 LLM calls each), writes
nothing, and reports per chunk the nodes / edges / properties added and
removed against current staging, and answerability `current` vs `proposed`.
The proof is: the baseline does not flip answerability, the proposal does.
Paste the relevant part of the output into the dossier's Proof section.
````

**`references/dossier-format.md`:** change the Proof row's content cell to "`artmind views test` output for a view; `artmind review reextract` baseline and proposal output (shape diff + answerability) for a schema or prompt change".

**`artmind/reviews/validate.py`:** change the Proof warning to "section 'Proof' is empty: add `views test` output, or `review reextract` output for a schema or prompt change".

- [ ] **Step 4: Update the docs**

1. **`CLAUDE.md`:** in the `artmind/reviews/` row:
   - change the module list to also name "`shape` (shape-audit bundle: sentences, graph subgraph via AGGREGATES/EXTRACTED_FROM + one RELATES_TO hop, staging, schema), `reextract` (dry-run re-extraction of evidence chunks under a proposed schema/meta — shape diff + answerability, writes nothing), `answerability` (pattern matcher)";
   - change the CLI list to `new/list/show/status/validate/reindex/leakcheck/shape/reextract`.

   In the routing bullet, add `shape` and `reextract`.

2. **`justfile`:** add after `review-leakcheck`:

   ```just
   # shape-audit bundle for a trace's evidence chunks  (usage: just review-shape <trace_id>)
   review-shape trace_id:
       uv run artmind review shape {{ trace_id }}

   # dry-run re-extraction under a proposed schema, writes nothing  (usage: just review-reextract <trace_id> <proposed_schema.yaml>)
   review-reextract trace_id schema:
       uv run artmind review reextract {{ trace_id }} --schema {{ schema }}
   ```

3. **Spec:**
   - **§7.3:** after the answerability-pattern JSON example, add: "Node and relationship steps alternate; a node step may give `class` and `name`, a relationship step `rel` and `dir` (out | in | any); `require` steps come last and index nodes or edges from 0. Matching is case-insensitive. `review shape --pattern` checks it against both the graph and the staging."
   - **§7.4:**
     - Add the bullet: "With neither `--schema` nor `--meta` it re-extracts under the current schema — the baseline that shows how much LLM variance alone moves the diff."
     - Change the like-for-like bullet to say the proposed side also skips the graph-derived name and property vocabularies, and compares with staging's pre-canonical `name`.
     - Note that `--schema` takes a full schema YAML, and that `reextract` needs no Neo4j.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run --group dev pytest test/test_reviews_skill.py test/test_reviews_validate.py test/test_cli_guide.py -q`, then `just --list | grep review`.

- [ ] **Step 6: Commit**

```bash
git add artmind/skills/artmind-review artmind/reviews/validate.py test/test_reviews_skill.py CLAUDE.md justfile docs/superpowers/specs/2026-10-11-query-review-loop-design.md
git commit -m "docs(reviews): skill and docs use review shape / reextract"
```

---

### Task 7: Full suite and the live proof

- [ ] **Step 1:** `just dev-test`. Everything passes.
- [ ] **Step 2:** Run `just dev-install`, then restart `serve` from the vault.
- [ ] **Step 3:** Run `artmind review shape <trace_id> --chunk <the 2025-01-24 chunk> --pattern <companion pattern>` against the dossier `rv-2026-10-11-e9a14f`'s trace. Expected: graph and staging both `matched: false`.
- [ ] **Step 4:** Write a proposed journal schema to `$TMPDIR` (the vault schema plus the dossier's `guidance:` block). Run `review reextract` on that chunk twice: once as the baseline, once with `--schema`. Record both in the dossier's Proof section, and update its Log.
- [ ] **Step 5:** Report.

---

## Self-review

**Spec coverage (P3 row of §10):**

| Spec item | Task |
|---|---|
| `review shape` (§7.3) | 3, 5 |
| `review reextract` (§7.4) | 1, 4, 5 |
| answerability pattern (§7.3, §7.4) | 2 |
| skill and docs | 6 |
| live proof | 7 |

**Additions:**
- `shape --pattern`: answerability for the shape audit itself, before any proposal.
- `reextract` baseline mode.

**Deferred to P4:** `review shape-report`, `--sample`, `review regressions`, `review verify`.
