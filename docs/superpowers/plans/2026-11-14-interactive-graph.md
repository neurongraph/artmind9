# Interactive graph Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The agent writes an ```` ```artmind-graph ```` fence (`{"domain", "cypher", "title"}`), the chat page draws it as an interactive Cytoscape graph from live Neo4j data through `POST /api/graph/run`, and the `artmind-present` skill says when to use it. The per-bubble **View as graph** link goes. Spec: `docs/superpowers/specs/2026-11-14-interactive-graph-design.md`.

**Architecture:** `artmind/webui/graph_routes.py` (`register_graph_routes(app, *, mode)`) runs read-only, `$domains`-only Cypher under a 15 s timeout, 2000-row and 500-node caps, and returns trimmed nodes and edges. `static/graph-render.js` replaces each `pre > code.language-artmind-graph` with a canvas, the same way `mermaid-render.js` handles mermaid. It loads vendored Cytoscape + fcose lazily and uses a full-window viewer factored out of `mermaid-render.js` into `static/viewer.js`. Phase 2 adds neighbour expansion, the admin graph drawer, and the Explore → Graph tab re-hosted on the same component.

**Tech Stack:** FastAPI, neo4j 6.2 driver (`neo4j.Query`, `neo4j.graph`), pytest + `TestClient`, vanilla JS, Cytoscape.js 3.35.0, cytoscape-fcose 2.2.0 (cose-base 2.2.0, layout-base 2.0.1; all MIT).

**Branch:** `interactive-graph`, cut from `master`. Commit after every task.

**Decisions taken while planning (not in the spec, or different from it):**

1. `artmind query graph text2cypher` has no graph mode; only `/api/explore/cypher` calls `generate_cypher(graph_mode=True)`. The skill tells the agent to take `--dryRun` Cypher and rewrite its `RETURN` (spec §9 rules out changing `text2cypher`).
2. `graph_query.serialize_record` drops node identity: `Record.data()` turns a node into its property dict. The collector therefore walks the raw driver values and calls `serialize_value` on each `Node` and `Relationship`.
3. Neighbour expansion (phase 2) goes through the same endpoint, as an `expand: <elementId>` field. The server owns the one-hop Cypher, so the browser never builds a literal.
4. The domain property on graph nodes is `_observation_count`. The wire key stays `observation_count`, as the spec names it.
5. Spec §8 Q1 (chat-ui fence provenance) is still open. Task 10, chat-ui support, is gated on the user's answer. Without that answer, phase 1 ships admin-only.

**Phase 3** (time slider, saved graph as a view, export) is not planned here: the spec gives it one line. It needs its own design.

---

## File structure

| File | Responsibility |
|---|---|
| `artmind/webui/graph_routes.py` (new) | Request model, `resolve_domains`, `prepare_cypher`, `cap_value`/`cap_properties`/`trim_node`/`trim_edge`, `GraphCollector`, `run_graph`, `RateLimiter`, `register_graph_routes` |
| `artmind/webui/app.py` | Register the graph routes (admin in phase 1; both after Task 10) |
| `artmind/webui/static/viewer.js` (new) | Shared full-window overlay: `window.ArtmindViewer.{button, open}` |
| `artmind/webui/static/mermaid-render.js` | Uses `ArtmindViewer` instead of its own overlay |
| `artmind/webui/static/graph-render.js` (new) | `window.renderArtmindGraph(scope)`, `window.ArtmindGraph.{mount, parseSpec, fetchGraph}` |
| `artmind/webui/static/graph-drawer.js` (new, phase 2) | Admin graph drawer: `window.ArtmindGraphDrawer.open(spec)` |
| `artmind/webui/static/vendor/{cytoscape.min.js,layout-base.js,cose-base.js,cytoscape-fcose.js,VENDORED.md}` (new) | Vendored libraries + versions and licences |
| `artmind/webui/static/{app.js,conversations.js,views.js}` | Call `renderArtmindGraph` wherever `renderMermaid` is called |
| `artmind/webui/static/graph.js` | Explore → Graph: comment fix (phase 1); Run + canvas (phase 2) |
| `artmind/webui/static/style.css` | `.viewer-*` (renamed from `.mermaid-*` overlay), `.agraph-*`, drawer; `.graph-link` removed |
| `artmind/webui/templates/{admin.html,dashboard.html,index.html}` | Script tags |
| `artmind/webui/explore_routes.py` (phase 2) | `run_cypher` in the generate response; `domains` on the link request |
| `artmind/webui/profiles.py` | `artmind-present` in `QA_PROFILE` and `ADMIN_PROFILE` |
| `artmind/skills/artmind-present/SKILL.md` (new) | The presentation skill |
| `artmind/skills/artmind-query/SKILL.md` | One routing line |
| `artmind/opencode/agent/{artmind.md,artmind-admin.md}` | Name the new skill |
| `CLAUDE.md` | Repo-layout rows |
| Deleted: `artmind/webui/static/admin-graph.js` | |
| Tests: `test/test_graph_routes.py` (new), `test/test_present_skill.py` (new), `test/test_graph_tab.py`, `test/test_webui_app.py`, `test/test_webui_profiles.py`, `test/test_explore_routes.py` | |

Run the whole suite with `just dev-test`, and a single file with `uv run --group dev pytest test/<file> -q`.

---

# Phase 1 — block, endpoint, skill

### Task 1: Remove the per-bubble View as graph link

**Files:**
- Delete: `artmind/webui/static/admin-graph.js`
- Modify: `artmind/webui/templates/admin.html:123`, `artmind/webui/static/style.css:154-155`, `artmind/webui/static/graph.js:3-7,106`
- Test: `test/test_graph_tab.py`

- [ ] **Step 1: Change the tests to expect the link gone**

In `test/test_graph_tab.py` replace `test_admin_console_loads_chat_link_script_but_chat_ui_does_not` and the two `for name in ("graph.js", "admin-graph.js")` loops, and fix the module docstring:

```python
"""Markup and asset wiring for Explore -> Graph. The admin chat's per-message
"View as graph" link was removed (spec 2026-11-14 §1): graphs are drawn in the chat."""
```

```python
def test_no_page_loads_the_removed_chat_link_script():
    console = TestClient(create_app(template_name="admin.html", admin_routes=True))
    assert "admin-graph.js" not in console.get("/").text
    assert "admin-graph.js" not in TestClient(create_app(admin_routes=False)).get("/").text
    assert console.get("/static/admin-graph.js").status_code == 404


def test_static_assets_served():
    assert _admin().get("/static/graph.js").status_code == 200


def test_graph_js_never_assigns_innerhtml_from_data():
    text = _admin().get("/static/graph.js").text
    for match in re.finditer(r"innerHTML\s*[+]?=(?!=)\s*(.*)", text):
        assert match.group(1).startswith('"";'), match.group(0)
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run --group dev pytest test/test_graph_tab.py -q`
Expected: FAIL in `test_no_page_loads_the_removed_chat_link_script` (`admin-graph.js` is still in the page).

- [ ] **Step 3: Remove the link**

```bash
git rm artmind/webui/static/admin-graph.js
```

In `artmind/webui/templates/admin.html` delete the line `  <script src="{{ static_url('admin-graph.js') }}"></script>`.

In `artmind/webui/static/style.css` delete these two lines:

```css
.msg-user .graph-link { font-size: 12px; color: var(--text-muted); text-decoration: none; }
.msg-user .graph-link:hover { color: var(--text); text-decoration: underline; }
```

In `artmind/webui/static/graph.js` replace the header comment lines 3-7 with:

```js
// Explore -> Graph tab: question -> read-only Cypher -> Neo4j Browser.
// Uses el() and api() from dashboard.js. Nothing runs here: the operator edits
// the Cypher, then opens Neo4j Browser (which pre-fills its editor) and runs it.
// `/dashboard?tab=graph&q=<question>` pre-fills the question (old links keep working).
```

and change the comment `// Prefill from the admin chat's "View as graph" link.` to `// Prefill from a /dashboard?tab=graph&q= link.`

- [ ] **Step 4: Run the tests**

Run: `uv run --group dev pytest test/test_graph_tab.py test/test_webui_app.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add -A artmind/webui test/test_graph_tab.py
git commit -m "refactor(webui): remove the per-message View as graph link

Spec 2026-11-14 §1: the link sent the question, not the answer, through a second
LLM call to Neo4j Browser. /dashboard?tab=graph&q= keeps working."
```

---

### Task 2: Graph-route helpers (domains, Cypher checks, trimming)

**Files:**
- Create: `artmind/webui/graph_routes.py`
- Test: `test/test_graph_routes.py`

- [ ] **Step 1: Write the failing tests**

Create `test/test_graph_routes.py`:

```python
"""POST /api/graph/run (spec docs/superpowers/specs/2026-11-14-interactive-graph-design.md §2)."""

import pytest
from neo4j.graph import Graph, Node

from artmind.graph_query import serialize_value
from artmind.webui import graph_routes

SCOPED = (
    "MATCH (a:Entity)-[r:RELATES_TO]-(b:Entity) "
    "WHERE (a._domain IN $domains OR any(dom IN $domains WHERE a._domain STARTS WITH (dom + '.'))) "
    "RETURN a, r, b"
)


def _factory():
    g = Graph()

    def node(eid, labels=(), **props):
        return Node(g, eid, 0, list(labels), props)

    def rel(eid, a, b, type_="RELATES_TO", **props):
        r = g.relationship_type(type_)(g, eid, 0, props)
        r._start_node, r._end_node = a, b
        return r

    return node, rel


@pytest.fixture
def known(monkeypatch):
    monkeypatch.setattr(graph_routes, "_available_domains", lambda: ["personal_journal", "banking.reference"])


# ── domains ──────────────────────────────────────────────────────────

def test_resolve_domains_accepts_known_children_and_family_roots(known):
    assert graph_routes.resolve_domains("personal_journal") == ["personal_journal"]
    assert graph_routes.resolve_domains(["personal_journal.health"]) == ["personal_journal.health"]
    assert graph_routes.resolve_domains("banking") == ["banking"]  # root of banking.reference


def test_resolve_domains_refuses_unknown_and_empty(known):
    with pytest.raises(ValueError, match="unknown domain 'bankingx'"):
        graph_routes.resolve_domains("bankingx")
    with pytest.raises(ValueError):
        graph_routes.resolve_domains(" , ")


# ── Cypher checks ────────────────────────────────────────────────────

def test_prepare_cypher_appends_the_graph_limit():
    assert graph_routes.prepare_cypher(SCOPED, require_domains=True) == SCOPED + "\nLIMIT 100"


def test_prepare_cypher_keeps_an_explicit_limit():
    assert graph_routes.prepare_cypher(SCOPED + " LIMIT 5000;", require_domains=False) == SCOPED + " LIMIT 5000"


@pytest.mark.parametrize("cypher, message", [
    ("MATCH (n) DETACH DELETE n", "write operation"),
    ("MATCH (n) SET n.x = 1 RETURN n", "write operation"),
    ("CALL db.labels()", "CALL"),
    ("MATCH (n {name: $name}) WHERE n._domain IN $domains RETURN n", r"\$name"),
])
def test_prepare_cypher_refuses(cypher, message):
    with pytest.raises(ValueError, match=message):
        graph_routes.prepare_cypher(cypher, require_domains=False)


def test_prepare_cypher_requires_domains_only_when_asked():
    graph_routes.prepare_cypher("MATCH (n:Entity) RETURN n", require_domains=False)
    with pytest.raises(ValueError, match=r"\$domains"):
        graph_routes.prepare_cypher("MATCH (n:Entity) RETURN n", require_domains=True)


# ── trimming ─────────────────────────────────────────────────────────

def test_cap_value_trims_long_text_and_long_lists():
    assert graph_routes.cap_value("x" * 10) == "x" * 10
    assert graph_routes.cap_value("x" * 600) == "x" * 500 + "…"
    long_list = graph_routes.cap_value(["abcdef"] * 200)
    assert isinstance(long_list, str) and len(long_list) == 501
    assert graph_routes.cap_value([1, 2]) == [1, 2]
    assert graph_routes.cap_value(3) == 3


def test_cap_properties_keeps_forty_keys():
    capped = graph_routes.cap_properties({f"k{i:02}": i for i in range(45)})
    assert len(capped) == 40 and "k00" in capped and "k44" not in capped


def test_trim_node_reads_class_count_and_strips_internal_props():
    node, _ = _factory()
    raw = node("n1", ["Entity", "Doctor"], name="Dr Rao", _id="e1", _observation_count=7,
               _valid_from="2024-01-02", embedding=[0.1, 0.2], _prop_sources="{}")
    out = graph_routes.trim_node(serialize_value(raw))
    assert out["id"] == "n1" and out["labels"] == ["Doctor", "Entity"]
    assert out["name"] == "Dr Rao" and out["_id"] == "e1" and out["class"] == "Doctor"
    assert out["observation_count"] == 7 and out["_valid_from"] == "2024-01-02"
    assert "embedding" not in out["properties"] and "_prop_sources" not in out["properties"]


def test_trim_node_prefers_entity_class_and_counts_omitted_properties():
    node, _ = _factory()
    props = {f"p{i:02}": i for i in range(41)} | {"entity_class": "Physician"}
    out = graph_routes.trim_node(serialize_value(node("n1", ["Entity", "Doctor"], **props)))
    assert out["class"] == "Physician" and out["properties_omitted"] == 2


def test_trim_edge_names_its_meaning():
    node, rel = _factory()
    a, b = node("n1", ["Entity"]), node("n2", ["Entity"])
    out = graph_routes.trim_edge(serialize_value(rel("r1", a, b, rel_type="treated_by", observation_count=2)))
    assert out == {"id": "r1", "source": "n1", "target": "n2", "type": "RELATES_TO",
                   "rel_type": "treated_by", "properties": {"observation_count": 2, "rel_type": "treated_by"}}
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `uv run --group dev pytest test/test_graph_routes.py -q`
Expected: FAIL with `ImportError: cannot import name 'graph_routes'`.

- [ ] **Step 3: Write the helpers**

Create `artmind/webui/graph_routes.py`:

```python
"""`POST /api/graph/run`: run an ```artmind-graph``` block's Cypher read-only and
return the nodes and edges to draw (spec
docs/superpowers/specs/2026-11-14-interactive-graph-design.md §2).

Both UIs register it. `mode="admin"` takes any Cypher that passes the read-only
checks. `mode="qa"` (chat-ui) also needs `$domains` in the Cypher and is rate
limited per client address. This is presentation, not retrieval, so nothing is
traced. The log line names the domains and the statement length, never the
Cypher text, because chat-ui Cypher can carry user text.
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

QUERY_TIMEOUT_S = 15  # the views runner allows 60 s; a drawing should be quicker
ROW_CAP = 2000
NODE_CAP = 500
PROPERTY_KEYS_CAP = 40
PROPERTY_CHARS_CAP = 500
MAX_CYPHER_CHARS = 20_000
QA_RUNS_PER_MINUTE = 10


def _available_domains() -> list[str]:
    from artmind.cli import _get_available_domains

    return _get_available_domains()


def resolve_domains(value: "str | list[str]") -> list[str]:
    """Normalised domain list; each one must be a schema's domain, a dotted
    child of one, or the family root of one."""
    from artmind.graph_query import normalize_domains

    domains = normalize_domains(value)
    known = _available_domains()
    for domain in domains:
        if not any(domain == k or domain.startswith(k + ".") or k.startswith(domain + ".") for k in known):
            raise ValueError(f"unknown domain {domain!r}")
    return domains


def prepare_cypher(cypher: str, *, require_domains: bool) -> str:
    """The Cypher to run, or ValueError. Read-only (the session is READ_ACCESS
    too; this is the friendly check), no CALL, LIMIT 100 when absent, and no
    parameter but `$domains`."""
    from artmind.text2cypher import _PARAM_RE, _prepare_graph_cypher, validate_domain_scoped, validate_read_only

    validate_read_only(cypher)
    prepared = _prepare_graph_cypher(cypher)
    others = sorted({name for name in _PARAM_RE.findall(prepared) if name != "domains"})
    if others:
        named = ", ".join(f"${name}" for name in others)
        raise ValueError(f"only $domains may be a parameter; write other values as literals (found {named})")
    if require_domains:
        validate_domain_scoped(prepared)
    return prepared


def cap_value(value: Any) -> Any:
    """A value small enough for the wire: long text is cut at PROPERTY_CHARS_CAP
    characters, and a long list or map becomes its cut JSON text."""
    if isinstance(value, str):
        return value if len(value) <= PROPERTY_CHARS_CAP else value[:PROPERTY_CHARS_CAP] + "…"
    if isinstance(value, (list, dict)):
        text = json.dumps(value, ensure_ascii=False, default=str)
        return value if len(text) <= PROPERTY_CHARS_CAP else text[:PROPERTY_CHARS_CAP] + "…"
    return value


def cap_properties(props: dict) -> dict:
    return {key: cap_value(props[key]) for key in sorted(props)[:PROPERTY_KEYS_CAP]}


def node_class(labels: list[str], props: dict) -> str | None:
    if props.get("entity_class"):
        return str(props["entity_class"])
    rest = sorted(label for label in labels if label != "Entity")
    return rest[0] if rest else (labels[0] if labels else None)


def trim_node(serialized: dict) -> dict:
    """`serialize_value(node)` cut to what the canvas uses."""
    props = serialized.get("properties") or {}
    labels = sorted(serialized.get("labels") or [])
    out = {
        "id": serialized["id"],
        "labels": labels,
        "name": cap_value(props["name"]) if props.get("name") is not None else None,
        "_id": props.get("_id"),
        "class": node_class(labels, props),
        "observation_count": props.get("_observation_count", props.get("observation_count")),
        "_valid_from": props.get("_valid_from"),
        "properties": cap_properties(props),
    }
    if len(props) > PROPERTY_KEYS_CAP:
        out["properties_omitted"] = len(props) - PROPERTY_KEYS_CAP
    return out


def trim_edge(serialized: dict) -> dict:
    """`serialize_value(relationship)` cut to what the canvas uses."""
    props = serialized.get("properties") or {}
    return {
        "id": serialized["id"],
        "source": serialized["start_node_id"],
        "target": serialized["end_node_id"],
        "type": serialized["type"],
        "rel_type": props.get("rel_type"),
        "properties": cap_properties(props),
    }
```

- [ ] **Step 4: Run the tests**

Run: `uv run --group dev pytest test/test_graph_routes.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add artmind/webui/graph_routes.py test/test_graph_routes.py
git commit -m "feat(webui): graph-route helpers: domains, Cypher checks, payload trimming"
```

---

### Task 3: Collect nodes and edges from driver values, and run the query

**Files:**
- Modify: `artmind/webui/graph_routes.py`
- Test: `test/test_graph_routes.py`

- [ ] **Step 1: Write the failing tests**

Append to `test/test_graph_routes.py`:

```python
from neo4j import Record
from neo4j.graph import Path


class FakeSession:
    """Stands in for read_session(): records (Query, parameters), returns one batch per run."""

    def __init__(self, *batches):
        self.calls = []
        self._batches = list(batches)

    def run(self, query, parameters=None):
        self.calls.append((query, parameters))
        return iter(self._batches.pop(0) if self._batches else [])

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_collector_splits_paths_dedupes_and_ignores_scalars():
    node, rel = _factory()
    a, b = node("n1", ["Entity"], name="A"), node("n2", ["Entity"], name="B")
    r = rel("r1", a, b)
    c = graph_routes.GraphCollector()
    for value in (Path(a, r), [a, b], {"x": r}, 42, "text", None):
        c.add_value(value)
    out = c.result()
    assert [n["id"] for n in out["nodes"]] == ["n1", "n2"]
    assert [e["id"] for e in out["edges"]] == ["r1"]
    assert out["omitted"] == {"nodes": 0, "edges": 0}


def test_collector_caps_nodes_and_drops_their_edges():
    node, rel = _factory()
    a, b, c3 = node("n1", ["Entity"]), node("n2", ["Entity"]), node("n3", ["Entity"])
    c = graph_routes.GraphCollector(node_cap=2)
    for value in (a, b, c3, rel("r1", a, b), rel("r2", b, c3)):
        c.add_value(value)
    out = c.result()
    assert len(out["nodes"]) == 2 and [e["id"] for e in out["edges"]] == ["r1"]
    assert out["omitted"] == {"nodes": 1, "edges": 1}
    assert c.missing_endpoint_ids() == []  # n3 is omitted, not missing


def test_run_graph_sends_only_domains_with_the_timeout(monkeypatch):
    node, rel = _factory()
    a, b = node("n1", ["Entity", "Person"], name="Mummy"), node("n2", ["Entity", "Doctor"], name="Dr Rao")
    session = FakeSession([Record({"a": a, "r": rel("r1", a, b, rel_type="treated_by"), "b": b, "n": 3})])
    monkeypatch.setattr("artmind.graph_query.read_session", lambda: session)
    out = graph_routes.run_graph(SCOPED + "\nLIMIT 100", ["personal_journal"])
    assert len(session.calls) == 1
    query, params = session.calls[0]
    assert query.text == SCOPED + "\nLIMIT 100" and query.timeout == 15
    assert params == {"domains": ["personal_journal"]}
    assert {n["name"] for n in out["nodes"]} == {"Mummy", "Dr Rao"}
    assert out["edges"][0]["rel_type"] == "treated_by"
    assert out["truncated"] is False and out["rows_read"] == 1 and out["rows_capped"] is False
    assert out["limits"] == {"nodes": 500, "rows": 2000, "timeout_s": 15}


def test_run_graph_fetches_a_relationship_endpoint_that_was_not_returned(monkeypatch):
    node, rel = _factory()
    a = node("n1", ["Entity"], name="Mummy")
    shell = node("n9")  # the driver gives a bare node for an endpoint the query did not return
    full = _factory()[0]("n9", ["Entity", "Doctor"], name="Dr X")
    session = FakeSession([Record({"a": a, "r": rel("r1", a, shell)})], [Record({"n": full})])
    monkeypatch.setattr("artmind.graph_query.read_session", lambda: session)
    out = graph_routes.run_graph("MATCH (a)-[r]->() RETURN a, r LIMIT 5", ["d"])
    query, params = session.calls[1]
    assert query.text == graph_routes.ENDPOINT_CYPHER and params == {"ids": ["n9"]}
    assert {n["name"] for n in out["nodes"]} == {"Mummy", "Dr X"} and len(out["edges"]) == 1


def test_run_graph_stops_at_the_row_cap(monkeypatch):
    node, _ = _factory()
    session = FakeSession([Record({"n": node(f"n{i}", ["Entity"])}) for i in range(3)])
    monkeypatch.setattr("artmind.graph_query.read_session", lambda: session)
    monkeypatch.setattr(graph_routes, "ROW_CAP", 2)
    out = graph_routes.run_graph("MATCH (n) RETURN n LIMIT 3", ["d"])
    assert out["rows_read"] == 2 and out["rows_capped"] is True and out["truncated"] is True
    assert len(out["nodes"]) == 2
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run --group dev pytest test/test_graph_routes.py -q`
Expected: FAIL with `AttributeError: module 'artmind.webui.graph_routes' has no attribute 'GraphCollector'`.

- [ ] **Step 3: Write the collector and `run_graph`**

Append to `artmind/webui/graph_routes.py`:

```python
ENDPOINT_CYPHER = "MATCH (n) WHERE elementId(n) IN $ids RETURN n"


class GraphCollector:
    """Distinct nodes and relationships from raw driver values, capped at
    `node_cap` nodes. Paths split into both; maps and lists are walked; scalar
    columns are ignored. A relationship's endpoints are not added from the
    relationship (the driver gives a bare node there); they are fetched."""

    def __init__(self, node_cap: int = NODE_CAP):
        self.node_cap = node_cap
        self.nodes: dict[str, dict] = {}
        self.edges: dict[str, dict] = {}
        self.omitted_node_ids: set[str] = set()

    def add_value(self, value: Any) -> None:
        from neo4j.graph import Node, Path, Relationship

        if isinstance(value, Node):
            self.add_node(value)
        elif isinstance(value, Relationship):
            self.add_edge(value)
        elif isinstance(value, Path):
            for node in value.nodes:
                self.add_node(node)
            for rel in value.relationships:
                self.add_edge(rel)
        elif isinstance(value, dict):
            for item in value.values():
                self.add_value(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                self.add_value(item)

    def add_node(self, node: Any) -> None:
        from artmind.graph_query import serialize_value

        nid = node.element_id
        if nid in self.nodes or nid in self.omitted_node_ids:
            return
        if len(self.nodes) >= self.node_cap:
            self.omitted_node_ids.add(nid)
            return
        self.nodes[nid] = trim_node(serialize_value(node))

    def add_edge(self, rel: Any) -> None:
        from artmind.graph_query import serialize_value

        if rel.element_id not in self.edges:
            self.edges[rel.element_id] = trim_edge(serialize_value(rel))

    def missing_endpoint_ids(self) -> list[str]:
        missing: list[str] = []
        for edge in self.edges.values():
            for nid in (edge["source"], edge["target"]):
                if nid not in self.nodes and nid not in self.omitted_node_ids and nid not in missing:
                    missing.append(nid)
        return missing

    def result(self) -> dict:
        edges = [e for e in self.edges.values() if e["source"] in self.nodes and e["target"] in self.nodes]
        return {
            "nodes": list(self.nodes.values()),
            "edges": edges,
            "omitted": {"nodes": len(self.omitted_node_ids), "edges": len(self.edges) - len(edges)},
        }


def run_graph(cypher: str, domains: list[str], extra_params: dict | None = None) -> dict:
    """Run prepared Cypher in a READ_ACCESS session; at most ROW_CAP records
    are read. Endpoints of returned relationships that the query did not
    return are fetched by element id, up to the node cap."""
    from neo4j import Query

    from artmind.graph_query import read_session

    collector = GraphCollector()
    rows_read = 0
    rows_capped = False
    with read_session() as session:
        for record in session.run(Query(cypher, timeout=QUERY_TIMEOUT_S), {"domains": domains, **(extra_params or {})}):
            if rows_read >= ROW_CAP:
                rows_capped = True
                break
            rows_read += 1
            for value in record.values():
                collector.add_value(value)
        missing = collector.missing_endpoint_ids()
        room = max(0, NODE_CAP - len(collector.nodes))
        collector.omitted_node_ids.update(missing[room:])
        if missing[:room]:
            for record in session.run(Query(ENDPOINT_CYPHER, timeout=QUERY_TIMEOUT_S), {"ids": missing[:room]}):
                collector.add_value(record["n"])
    out = collector.result()
    omitted = out.pop("omitted")
    return {
        **out,
        "truncated": bool(omitted["nodes"] or omitted["edges"] or rows_capped),
        "omitted": omitted,
        "rows_read": rows_read,
        "rows_capped": rows_capped,
        "limits": {"nodes": NODE_CAP, "rows": ROW_CAP, "timeout_s": QUERY_TIMEOUT_S},
    }
```

- [ ] **Step 4: Run the tests**

Run: `uv run --group dev pytest test/test_graph_routes.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add artmind/webui/graph_routes.py test/test_graph_routes.py
git commit -m "feat(webui): collect drawable nodes and edges from a read-only graph run

15 s timeout, 2000-row and 500-node caps, missing relationship endpoints
fetched by element id; the response says what was left out."
```

---

### Task 4: The route, its two modes, and registration in the admin console

**Files:**
- Modify: `artmind/webui/graph_routes.py`, `artmind/webui/app.py:31-34,482-488`
- Test: `test/test_graph_routes.py`, `test/test_graph_tab.py`

- [ ] **Step 1: Write the failing tests**

Append to `test/test_graph_routes.py`:

```python
import logging

from fastapi import FastAPI
from fastapi.testclient import TestClient
from neo4j.exceptions import ClientError

RESULT = {"nodes": [{"id": "n1"}], "edges": [], "truncated": False, "omitted": {"nodes": 0, "edges": 0},
          "rows_read": 1, "rows_capped": False, "limits": {"nodes": 500, "rows": 2000, "timeout_s": 15}}


def _client(mode):
    app = FastAPI()
    graph_routes.register_graph_routes(app, mode=mode)
    return TestClient(app)


@pytest.fixture
def ran(monkeypatch, known):
    calls = []

    def fake(cypher, domains, extra_params=None):
        calls.append((cypher, domains, extra_params))
        return RESULT

    monkeypatch.setattr(graph_routes, "run_graph", fake)
    return calls


def test_admin_run_returns_the_graph_and_the_cypher_it_ran(ran):
    r = _client("admin").post("/api/graph/run", json={"domain": "personal_journal", "cypher": SCOPED})
    assert r.status_code == 200
    body = r.json()
    assert body["cypher"] == SCOPED + "\nLIMIT 100" and body["domains"] == ["personal_journal"]
    assert body["nodes"] == [{"id": "n1"}]
    assert ran == [(SCOPED + "\nLIMIT 100", ["personal_journal"], None)]


def test_admin_runs_unscoped_cypher_but_qa_refuses_it(ran):
    body = {"domain": "personal_journal", "cypher": "MATCH (n:Entity) RETURN n"}
    assert _client("admin").post("/api/graph/run", json=body).status_code == 200
    r = _client("qa").post("/api/graph/run", json=body)
    assert r.status_code == 400 and "$domains" in r.json()["detail"]


@pytest.mark.parametrize("body", [
    {"domain": "personal_journal", "cypher": "MATCH (n) DETACH DELETE n"},
    {"domain": "personal_journal", "cypher": "CALL db.labels() YIELD label RETURN label"},
    {"domain": "nowhere", "cypher": SCOPED},
])
def test_refusals_are_400_and_nothing_runs(ran, body):
    assert _client("admin").post("/api/graph/run", json=body).status_code == 400
    assert ran == []


@pytest.mark.parametrize("body", [{"domain": "d"}, {"cypher": SCOPED}, {"domain": "d", "cypher": "  "}])
def test_malformed_requests_are_422(ran, body):
    assert _client("admin").post("/api/graph/run", json=body).status_code == 422


def test_qa_is_rate_limited_per_client(ran):
    client = _client("qa")
    body = {"domain": "personal_journal", "cypher": SCOPED}
    codes = [client.post("/api/graph/run", json=body).status_code for _ in range(11)]
    assert codes == [200] * 10 + [429]


def test_rate_limiter_window_slides():
    now = [0.0]
    limiter = graph_routes.RateLimiter(2, window_s=60, clock=lambda: now[0])
    assert limiter.allow("a") and limiter.allow("a") and not limiter.allow("a")
    assert limiter.allow("b")
    now[0] = 60.0
    assert limiter.allow("a")


def test_timeout_is_504_and_other_neo4j_errors_502_in_one_line(monkeypatch, known):
    def timed_out(*_a, **_k):
        raise ClientError._hydrate_neo4j(code="Neo.ClientError.Transaction.TransactionTimedOutClientConfiguration",
                                          message="terminated")

    monkeypatch.setattr(graph_routes, "run_graph", timed_out)
    body = {"domain": "personal_journal", "cypher": SCOPED}
    r = _client("admin").post("/api/graph/run", json=body)
    assert r.status_code == 504 and "15 s" in r.json()["detail"]

    def syntax(*_a, **_k):
        raise ClientError._hydrate_neo4j(code="Neo.ClientError.Statement.SyntaxError", message="bad\nat line 2")

    monkeypatch.setattr(graph_routes, "run_graph", syntax)
    r = _client("admin").post("/api/graph/run", json=body)
    assert r.status_code == 502 and r.json()["detail"] == "Neo4j: bad"


def test_the_log_line_never_holds_the_cypher(ran, caplog):
    caplog.set_level(logging.INFO, logger="artmind.webui.graph_routes")
    secret = SCOPED.replace("RETURN a, r, b", "AND a.name = 'Secret Person' RETURN a, r, b")
    _client("admin").post("/api/graph/run", json={"domain": "personal_journal", "cypher": secret})
    assert "graph run" in caplog.text and "Secret Person" not in caplog.text


def test_unknown_mode_is_refused():
    with pytest.raises(ValueError):
        graph_routes.register_graph_routes(FastAPI(), mode="benchmark")
```

Append to `test/test_graph_tab.py`:

```python
def test_graph_run_route_is_on_the_admin_console_only():
    body = {"domain": "d"}  # incomplete: 422 proves the route exists without touching Neo4j
    assert _admin().post("/api/graph/run", json=body).status_code == 422
    assert TestClient(create_app(admin_routes=False)).post("/api/graph/run", json=body).status_code == 404
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run --group dev pytest test/test_graph_routes.py test/test_graph_tab.py -q`
Expected: FAIL with `AttributeError: ... has no attribute 'register_graph_routes'`.

- [ ] **Step 3: Write the route**

Add these imports at the top of `artmind/webui/graph_routes.py`, below `import logging`:

```python
import threading
import time
from collections import deque

import anyio.to_thread
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, field_validator
```

Append:

```python
class GraphRunRequest(BaseModel):
    domain: str | list[str]
    cypher: str

    @field_validator("cypher")
    @classmethod
    def _cypher(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("cypher must not be empty")
        if len(value) > MAX_CYPHER_CHARS:
            raise ValueError(f"cypher is longer than {MAX_CYPHER_CHARS} characters")
        return value


class RateLimiter:
    """At most `limit` calls per `window_s` seconds per key (a sliding window)."""

    def __init__(self, limit: int, window_s: float = 60.0, clock=time.monotonic):
        self.limit = limit
        self.window_s = window_s
        self._clock = clock
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = self._clock()
        with self._lock:
            hits = self._hits.setdefault(key, deque())
            while hits and now - hits[0] >= self.window_s:
                hits.popleft()
            if len(hits) >= self.limit:
                return False
            hits.append(now)
            return True


def _db_error(exc: Exception) -> HTTPException:
    code = getattr(exc, "code", None) or ""
    if "TransactionTimedOut" in code:
        return HTTPException(status_code=504, detail=f"the query ran longer than {QUERY_TIMEOUT_S} s and was stopped")
    message = getattr(exc, "message", None) or str(exc) or type(exc).__name__
    return HTTPException(status_code=502, detail=f"Neo4j: {message.splitlines()[0]}")


def register_graph_routes(app: FastAPI, *, mode: str) -> FastAPI:
    if mode not in ("admin", "qa"):
        raise ValueError(f"mode must be 'admin' or 'qa', not {mode!r}")
    limiter = RateLimiter(QA_RUNS_PER_MINUTE) if mode == "qa" else None

    @app.post("/api/graph/run")
    async def api_graph_run(payload: GraphRunRequest, request: Request):
        from neo4j.exceptions import DriverError, Neo4jError

        if limiter is not None:
            client = request.client.host if request.client else "unknown"
            if not limiter.allow(client):
                raise HTTPException(status_code=429, detail=f"at most {QA_RUNS_PER_MINUTE} graph runs a minute; try again shortly")
        try:
            domains = resolve_domains(payload.domain)
            cypher = prepare_cypher(payload.cypher, require_domains=mode == "qa")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        try:
            result = await anyio.to_thread.run_sync(run_graph, cypher, domains)
        except (Neo4jError, DriverError) as exc:
            raise _db_error(exc) from exc
        logger.info(
            "graph run: mode=%s domains=%s chars=%d nodes=%d edges=%d truncated=%s",
            mode, ",".join(domains), len(cypher), len(result["nodes"]), len(result["edges"]), result["truncated"],
        )
        return {"domains": domains, "cypher": cypher, **result}

    return app
```

In `artmind/webui/app.py` add the import beside the other route imports:

```python
from artmind.webui.graph_routes import register_graph_routes
```

and add a line to the `if admin_routes:` block, after `register_explore_routes(app)`:

```python
        register_graph_routes(app, mode="admin")
```

- [ ] **Step 4: Run the tests**

Run: `uv run --group dev pytest test/test_graph_routes.py test/test_graph_tab.py test/test_webui_app.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add artmind/webui/graph_routes.py artmind/webui/app.py test/test_graph_routes.py test/test_graph_tab.py
git commit -m "feat(webui): POST /api/graph/run on the admin console

Read-only, \$domains-only Cypher; qa mode adds the \$domains requirement and
10 runs/minute per client. Timeout 504, Neo4j errors one line, nothing traced,
the log names domains and length only."
```

---

### Task 5: Vendor Cytoscape and fcose

**Files:**
- Create: `artmind/webui/static/vendor/{cytoscape.min.js,layout-base.js,cose-base.js,cytoscape-fcose.js,VENDORED.md}`
- Test: `test/test_webui_app.py`

- [ ] **Step 1: Write the failing test**

Append to `test/test_webui_app.py`:

```python
GRAPH_LIBS = ("cytoscape.min.js", "layout-base.js", "cose-base.js", "cytoscape-fcose.js")


def test_graph_libraries_are_vendored_with_their_licences():
    from artmind.webui.app import WEBUI_DIR

    vendor = WEBUI_DIR / "static" / "vendor"
    record = (vendor / "VENDORED.md").read_text()
    for name in GRAPH_LIBS:
        assert (vendor / name).stat().st_size > 10_000, name
        assert name in record
    for pinned in ("cytoscape 3.35.0", "cytoscape-fcose 2.2.0", "cose-base 2.2.0", "layout-base 2.0.1"):
        assert pinned in record
    assert record.count("MIT License") >= 4
```

- [ ] **Step 2: Run it to see it fail**

Run: `uv run --group dev pytest test/test_webui_app.py::test_graph_libraries_are_vendored_with_their_licences -q`
Expected: FAIL with `FileNotFoundError` for `VENDORED.md`.

- [ ] **Step 3: Download into a fresh directory and copy**

`npm pack` downloads tarballs and runs no package scripts. Keep the downloads in their own empty directory.

```bash
tmp=$(mktemp -d)
(cd "$tmp" && npm pack cytoscape@3.35.0 cytoscape-fcose@2.2.0 cose-base@2.2.0 layout-base@2.0.1)
for t in "$tmp"/*.tgz; do mkdir -p "${t%.tgz}" && tar -xzf "$t" -C "${t%.tgz}"; done
V=artmind/webui/static/vendor
cp "$tmp/cytoscape-3.35.0/package/dist/cytoscape.min.js" "$V/"
cp "$tmp/layout-base-2.0.1/package/layout-base.js" "$V/"
cp "$tmp/cose-base-2.2.0/package/cose-base.js" "$V/"
cp "$tmp/cytoscape-fcose-2.2.0/package/cytoscape-fcose.js" "$V/"
wc -c "$V"/cytoscape.min.js "$V"/layout-base.js "$V"/cose-base.js "$V"/cytoscape-fcose.js
```

Expected sizes (measured 2026-11-14): 442,752 / 147,958 / 118,906 / 57,239 bytes. Each file is a UMD bundle. Loaded in the order cytoscape → layout-base → cose-base → cytoscape-fcose, they set `window.cytoscape`, `layoutBase`, `coseBase` and `cytoscapeFcose`, and fcose registers itself with the global `cytoscape`.

Record the existing vendored libraries too. Read their versions with `head -c 300 "$V"/marked.min.js "$V"/purify.min.js` and `grep -o 'version:"[0-9.]*"' "$V"/mermaid.min.js | head -1`. Then write `artmind/webui/static/vendor/VENDORED.md` with the versions you read in place of each `<version>`:

```markdown
# Vendored browser libraries

Served from `/static/vendor/`, shipped in the wheel (`webui/static/**/*`). No build step: the files are
copied from the npm tarballs as published. Update by re-running the commands in
`docs/superpowers/plans/2026-11-14-interactive-graph.md` Task 5 with the new versions.

| File | Package | Version | Licence | Loaded by |
|---|---|---|---|---|
| `marked.min.js` | marked | <version> | MIT | every page |
| `purify.min.js` | dompurify | <version> | Apache-2.0 / MPL-2.0 | every page |
| `mermaid.min.js` | mermaid | <version> | MIT | `mermaid-render.js`, first diagram only |
| `cytoscape.min.js` | cytoscape 3.35.0 | 3.35.0 | MIT | `graph-render.js`, first graph only |
| `layout-base.js` | layout-base 2.0.1 | 2.0.1 | MIT | `graph-render.js` (fcose dependency) |
| `cose-base.js` | cose-base 2.2.0 | 2.2.0 | MIT | `graph-render.js` (fcose dependency) |
| `cytoscape-fcose.js` | cytoscape-fcose 2.2.0 | 2.2.0 | MIT | `graph-render.js` (force layout) |

## Licences
```

Then append each new package's licence text under its own heading:

```bash
for p in cytoscape-3.35.0 layout-base-2.0.1 cose-base-2.2.0 cytoscape-fcose-2.2.0; do
  printf '\n### %s\n\n```\n' "$p" >> "$V/VENDORED.md"
  cat "$tmp/$p/package/LICENSE" >> "$V/VENDORED.md"
  printf '```\n' >> "$V/VENDORED.md"
done
grep -c "MIT License" "$V/VENDORED.md"
```

Expected: `4` or more. If a LICENSE file has a different first line (for example `Copyright (c) ...`), add the line `MIT License` above that block by hand.

- [ ] **Step 4: Run the test**

Run: `uv run --group dev pytest test/test_webui_app.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add artmind/webui/static/vendor test/test_webui_app.py
git commit -m "chore(webui): vendor cytoscape 3.35.0 and cytoscape-fcose 2.2.0

With cose-base 2.2.0 and layout-base 2.0.1 (fcose's dependencies). All MIT;
versions, sizes and licence texts in static/vendor/VENDORED.md."
```

---

### Task 6: Factor the full-window viewer out of mermaid-render.js

**Files:**
- Create: `artmind/webui/static/viewer.js`
- Modify: `artmind/webui/static/mermaid-render.js:62-199`, `artmind/webui/static/style.css:201-217`, the three templates
- Test: `test/test_webui_app.py:96-106`

- [ ] **Step 1: Update the tests**

In `test/test_webui_app.py` replace `test_mermaid_diagram_has_expand_and_copy_source_controls` with:

```python
def test_mermaid_diagram_has_expand_and_copy_source_controls():
    """A rendered diagram has expand (the shared zoom/pan viewer) and copy-source
    buttons, and the stylesheet that styles them ships with every page."""
    from artmind.webui.app import WEBUI_DIR

    script = (WEBUI_DIR / "static" / "mermaid-render.js").read_text()
    viewer = (WEBUI_DIR / "static" / "viewer.js").read_text()
    style = (WEBUI_DIR / "static" / "style.css").read_text()
    assert '"expand"' in script and '"copy source"' in script
    assert "navigator.clipboard.writeText(host._mermaidSource)" in script
    assert "ArtmindViewer.open(" in script and "window.ArtmindViewer" in viewer
    for selector in (".mermaid-toolbar", ".viewer-overlay", ".viewer-bar", ".viewer-btn"):
        assert selector in style
    assert ".mermaid-overlay" not in style and ".mermaid-btn" not in style


@pytest.mark.parametrize("template_name", ["index.html", "admin.html", "dashboard.html"])
def test_viewer_loads_before_the_renderers(template_name):
    client = TestClient(create_app(registry=SessionRegistry(client_factory=FakeBackend), template_name=template_name))
    html = client.get("/").text
    assert html.index("/static/viewer.js?v=") < html.index("/static/mermaid-render.js?v=")
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run --group dev pytest test/test_webui_app.py -q -k "viewer or expand_and_copy"`
Expected: FAIL (`viewer.js` missing).

- [ ] **Step 3: Write `viewer.js`**

Create `artmind/webui/static/viewer.js`:

```js
"use strict";

// The full-window viewer shared by mermaid-render.js and graph-render.js: a dialog
// overlay with a button bar. Esc closes it, and focus goes back to the opener.
// Callers put their own content (and zoom) in `overlay`; the bar stays on top.
//   ArtmindViewer.open({label, buttons: [[label, title, onClick], ...], keys: {"+": fn},
//                       onResize, onClose, className}) -> {overlay, bar, close}
//   ArtmindViewer.button(label, title, onClick) -> <button class="viewer-btn">
(function () {
  function button(label, title, onClick) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "viewer-btn";
    b.textContent = label;
    b.title = title;
    b.addEventListener("click", onClick);
    return b;
  }

  function open({ label = "Viewer", buttons = [], keys = {}, onResize = null, onClose = null, className = "" } = {}) {
    const opener = document.activeElement;
    const overlay = document.createElement("div");
    overlay.className = className ? `viewer-overlay ${className}` : "viewer-overlay";
    overlay.setAttribute("role", "dialog");
    overlay.setAttribute("aria-modal", "true");
    overlay.setAttribute("aria-label", label);
    const bar = document.createElement("div");
    bar.className = "viewer-bar";

    function onKey(e) {
      if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); close(); }
      else if (keys[e.key]) keys[e.key]();
    }
    function close() {
      document.removeEventListener("keydown", onKey, true);
      if (onResize) window.removeEventListener("resize", onResize);
      overlay.remove();
      if (onClose) onClose();
      if (opener && opener.focus) opener.focus();
    }

    for (const [text, title, fn] of buttons) bar.appendChild(button(text, title, fn));
    const closeBtn = button("close", "Close (Esc)", close);
    bar.appendChild(closeBtn);
    overlay.appendChild(bar);
    document.addEventListener("keydown", onKey, true);
    if (onResize) window.addEventListener("resize", onResize);
    document.body.appendChild(overlay);
    closeBtn.focus();
    return { overlay, bar, close };
  }

  window.ArtmindViewer = { button, open };
})();
```

- [ ] **Step 4: Use it in `mermaid-render.js`**

In `artmind/webui/static/mermaid-render.js`:

1. Delete the local `function button(label, title, onClick) { ... }` (lines 62-70). Add this line at the top of the IIFE, below `let counter = 0;`:

```js
  const button = (label, title, onClick) => window.ArtmindViewer.button(label, title, onClick);
```

2. Replace the whole `function expand(host) { ... }` (lines 94-199) with:

```js
  function expand(host) {
    const source = host.querySelector("svg");
    if (!source) return;
    const svg = source.cloneNode(true);
    // Mermaid sizes its svg to 100% of the column; give it its natural size and
    // scale the whole stage instead.
    const box = source.viewBox && source.viewBox.baseVal;
    const natural = box && box.width ? { w: box.width, h: box.height } : { w: source.getBBox().width, h: source.getBBox().height };
    svg.removeAttribute("style");
    svg.setAttribute("width", natural.w);
    svg.setAttribute("height", natural.h);

    let scale = 1, x = 0, y = 0;
    const stage = document.createElement("div");
    stage.className = "viewer-stage";
    stage.appendChild(svg);
    const apply = () => { stage.style.transform = `translate(${x}px, ${y}px) scale(${scale})`; };
    const zoomAt = (factor, cx, cy) => {
      const next = Math.min(MAX_SCALE, Math.max(MIN_SCALE, scale * factor));
      const k = next / scale;
      x = cx - (cx - x) * k;
      y = cy - (cy - y) * k;
      scale = next;
      apply();
    };
    let view = null;
    const fit = () => {
      const vw = view.overlay.clientWidth, vh = view.overlay.clientHeight;
      scale = Math.min((vw * 0.94) / natural.w, (vh * 0.86) / natural.h, 4);
      x = (vw - natural.w * scale) / 2;
      y = (vh - natural.h * scale) / 2;
      apply();
    };
    const zoomCentre = (factor) => zoomAt(factor, view.overlay.clientWidth / 2, view.overlay.clientHeight / 2);

    view = window.ArtmindViewer.open({
      label: "Diagram",
      className: "pan",
      buttons: [
        ["−", "Zoom out", () => zoomCentre(1 / 1.25)],
        ["+", "Zoom in", () => zoomCentre(1.25)],
        ["fit", "Fit to window", fit],
        ["100%", "Actual size", () => zoomCentre(1 / scale)],
      ],
      keys: { "+": () => zoomCentre(1.25), "=": () => zoomCentre(1.25), "-": () => zoomCentre(1 / 1.25), "0": fit },
      onResize: fit,
    });
    const overlay = view.overlay;
    overlay.insertBefore(stage, view.bar);

    // Pointer drag pans; a press that never moved on the backdrop closes.
    let drag = null;
    overlay.addEventListener("pointerdown", (e) => {
      if (e.target.closest(".viewer-bar")) return;
      drag = { px: e.clientX, py: e.clientY, x, y, moved: false, onDiagram: !!e.target.closest("svg") };
      overlay.setPointerCapture(e.pointerId);
      overlay.classList.add("panning");
    });
    overlay.addEventListener("pointermove", (e) => {
      if (!drag) return;
      const dx = e.clientX - drag.px, dy = e.clientY - drag.py;
      if (Math.abs(dx) + Math.abs(dy) > 3) drag.moved = true;
      x = drag.x + dx;
      y = drag.y + dy;
      apply();
    });
    overlay.addEventListener("pointerup", () => {
      if (drag && !drag.moved && !drag.onDiagram) view.close();
      drag = null;
      overlay.classList.remove("panning");
    });
    overlay.addEventListener("pointercancel", () => { drag = null; overlay.classList.remove("panning"); });
    overlay.addEventListener("wheel", (e) => {
      e.preventDefault();
      zoomAt(Math.exp(-e.deltaY * (e.ctrlKey ? 0.01 : 0.0015)), e.clientX, e.clientY);
    }, { passive: false });
    overlay.addEventListener("dblclick", (e) => { if (!e.target.closest(".viewer-bar")) fit(); });
    fit();
  }
```

3. Update the header comment line `// A rendered diagram has a hover toolbar: "expand" opens it full-window with` so it adds `(the shared viewer in viewer.js)`.

- [ ] **Step 5: Rename the overlay CSS**

In `artmind/webui/static/style.css` replace lines 201-217 (from `.mermaid-btn {` to `.mermaid-viewer-bar .mermaid-btn { ... }`) with:

```css
.viewer-btn {
  padding: 3px 9px; border-radius: 6px; border: 1px solid var(--border);
  background: var(--bg-elev); color: var(--text-muted);
  font-size: 11px; cursor: pointer; user-select: none;
}
.viewer-btn:hover { color: var(--text); }
/* ── full-window viewer (viewer.js) ─────────────────────────────── */
.viewer-overlay {
  position: fixed; inset: 0; z-index: 1000; overflow: hidden;
  background: var(--bg);
}
.viewer-overlay.pan { cursor: grab; touch-action: none; }
.viewer-overlay.panning { cursor: grabbing; }
.viewer-stage { position: absolute; top: 0; left: 0; transform-origin: 0 0; will-change: transform; }
.viewer-stage svg { display: block; max-width: none; }
.viewer-bar {
  position: absolute; top: 12px; right: 12px; z-index: 1; display: flex; gap: 6px; cursor: default;
}
.viewer-bar .viewer-btn { font-size: 13px; padding: 5px 12px; }
```

Then check that nothing still uses the old names:

```bash
grep -rn "mermaid-btn\|mermaid-overlay\|mermaid-stage\|mermaid-viewer-bar" artmind/webui
```

Expected: no output.

- [ ] **Step 6: Load `viewer.js` on every page**

In `artmind/webui/templates/index.html`, `admin.html` and `dashboard.html`, add this line directly above the `mermaid-render.js` script tag:

```html
  <script src="{{ static_url('viewer.js') }}"></script>
```

- [ ] **Step 7: Run the tests**

Run: `uv run --group dev pytest test/test_webui_app.py test/test_graph_tab.py test/test_insights_tabs.py -q`
Expected: PASS.

- [ ] **Step 8: Check mermaid by hand**

Start `admin-ui` (see Task 11, Step 2 for the launch entry). Open a conversation that contains a mermaid diagram, or run `views` on the dashboard with a `mermaid_graph` view. Press **expand**, then check wheel zoom, drag pan, `+`/`-`/`0`, Esc, and that a click on the backdrop closes the viewer. Use `preview_click` on `.mermaid-toolbar .viewer-btn` and `preview_screenshot`.

- [ ] **Step 9: Commit**

```bash
git add artmind/webui/static/viewer.js artmind/webui/static/mermaid-render.js artmind/webui/static/style.css artmind/webui/templates test/test_webui_app.py
git commit -m "refactor(webui): share the full-window viewer (viewer.js)

mermaid-render.js keeps its zoom and pan; the overlay shell, button bar and
Esc/focus handling move to ArtmindViewer so graph-render.js can reuse them."
```

---

### Task 7: graph-render.js — draw artmind-graph blocks

**Files:**
- Create: `artmind/webui/static/graph-render.js`
- Modify: `artmind/webui/static/app.js:186,401`, `artmind/webui/static/conversations.js:311`, `artmind/webui/static/views.js:63`, `artmind/webui/static/style.css` (append), `artmind/webui/templates/admin.html`, `artmind/webui/templates/dashboard.html`
- Test: `test/test_webui_app.py`

- [ ] **Step 1: Write the failing tests**

Append to `test/test_webui_app.py`:

```python
@pytest.mark.parametrize("template_name", ["admin.html", "dashboard.html"])
def test_admin_pages_load_the_graph_renderer_lazily(template_name):
    client = TestClient(create_app(registry=SessionRegistry(client_factory=FakeBackend),
                                   template_name=template_name, admin_routes=True))
    html = client.get("/" if template_name == "admin.html" else "/dashboard").text
    assert "/static/graph-render.js?v=" in html
    libs = html.split('data-graph-libs="', 1)[1].split('"', 1)[0].split()
    assert [lib.split("?")[0] for lib in libs] == [f"/static/vendor/{name}" for name in GRAPH_LIBS]
    assert '<script src="/static/vendor/cytoscape' not in html
    assert html.index("/static/viewer.js?v=") < html.index("/static/graph-render.js?v=")


@pytest.mark.parametrize("script", ["app.js", "conversations.js", "views.js"])
def test_markdown_renderers_call_render_artmind_graph(script):
    """Wherever a mermaid block can appear, an artmind-graph block is drawn too."""
    from artmind.webui.app import WEBUI_DIR

    source = (WEBUI_DIR / "static" / script).read_text()
    assert source.count("renderMermaid(") == source.count("renderArtmindGraph(")


@pytest.mark.parametrize("script", ["graph-render.js", "viewer.js"])
def test_graph_scripts_never_use_innerhtml(script):
    """Conversations hold private corpus content: graph text goes through textContent only."""
    from artmind.webui.app import WEBUI_DIR

    assert "innerHTML" not in (WEBUI_DIR / "static" / script).read_text()


def test_graph_renderer_targets_the_fence_and_the_endpoint():
    from artmind.webui.app import WEBUI_DIR

    source = (WEBUI_DIR / "static" / "graph-render.js").read_text()
    assert "pre > code.language-artmind-graph" in source
    assert '"/api/graph/run"' in source
    assert "JSON.parse(" in source
    style = (WEBUI_DIR / "static" / "style.css").read_text()
    for selector in (".agraph", ".agraph-canvas", ".agraph-toolbar", ".agraph-inspector"):
        assert selector in style
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run --group dev pytest test/test_webui_app.py -q -k "graph"`
Expected: FAIL.

- [ ] **Step 3: Write `graph-render.js`**

Create `artmind/webui/static/graph-render.js`:

```js
"use strict";

// ```artmind-graph blocks in rendered markdown (admin chat, Views tab, Explore -> Graph; chat-ui
// once its endpoint is on). Spec docs/superpowers/specs/2026-11-14-interactive-graph-design.md §3.
//
// marked turns the fence into <pre><code class="language-artmind-graph">. This replaces each such
// block with an interactive Cytoscape canvas drawn from POST /api/graph/run. The data is live, so
// a reloaded conversation redraws. The fence is JSON {"domain", "cypher", "title"?}; a fence that
// does not parse stays a code block and is not run. Cytoscape and the fcose layout are vendored
// and loaded on the first block only (data-graph-libs, in load order). Graph text reaches the
// page only through textContent and Cytoscape labels, never as HTML: conversations hold private
// corpus content.
//   renderArtmindGraph(scope)                -- after a node's markdown is in the DOM; idempotent
//   ArtmindGraph.mount(host, spec, opts)     -- draw one graph into host (the admin drawer uses it)
(function () {
  const LIBS = ((document.currentScript && document.currentScript.dataset.graphLibs) || "").split(/\s+/).filter(Boolean);
  const STRUCTURAL = new Set(["Document", "DocChunk", "Passage", "DocSummary", "Observation"]);
  // 4.5:1 or better against --bg in each theme (checked 2026-11-14).
  const PALETTE = {
    light: ["#2f6db5", "#b8562a", "#2e7d4f", "#8a4fb0", "#b0386a", "#6b6b18", "#1a7479", "#a3461f", "#5a5fc4", "#7a5a2e"],
    dark: ["#6ea8ec", "#f0915e", "#62c48a", "#c08ce6", "#ec7fae", "#c9c45a", "#5cc9d1", "#e8a06a", "#9ba0f2", "#d4ad74"],
  };
  const LAYOUTS = [
    { label: "force", options: { name: "fcose", animate: false, randomize: true, nodeRepulsion: 6000, idealEdgeLength: 90 } },
    { label: "concentric", options: { name: "concentric", animate: false, minNodeSpacing: 18 } },
    { label: "tree", options: { name: "breadthfirst", animate: false, directed: true, spacingFactor: 1.1 } },
  ];
  let loading = null;

  function el(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined) n.textContent = text;
    return n;
  }
  const themeName = () => (document.documentElement.dataset.theme === "dark" ? "dark" : "light");
  function cssVar(name, fallback) {
    const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return v || fallback;
  }

  function loadScript(src) {
    return new Promise((resolve, reject) => {
      const s = document.createElement("script");
      s.src = src;
      s.onload = resolve;
      s.onerror = () => reject(new Error("the graph library failed to load"));
      document.head.appendChild(s);
    });
  }
  function load() {
    if (window.cytoscape) return Promise.resolve(window.cytoscape);
    if (!loading) {
      loading = LIBS.reduce((p, src) => p.then(() => loadScript(src)), Promise.resolve())
        .then(() => window.cytoscape)
        .catch((err) => { loading = null; throw err; });
    }
    return loading;
  }

  function hashIndex(text, n) {
    let h = 2166136261;
    for (let i = 0; i < text.length; i++) { h ^= text.charCodeAt(i); h = Math.imul(h, 16777619); }
    return (h >>> 0) % n;
  }
  function classColour(cls) {
    const p = PALETTE[themeName()];
    return p[hashIndex(String(cls || ""), p.length)];
  }
  const nodeSize = (count) => Math.max(18, Math.min(56, 18 + 7 * Math.log2(1 + (Number(count) || 0))));
  function layoutOptions(i) {
    if (i === 0 && !window.cytoscapeFcose) return { name: "cose", animate: false };
    return LAYOUTS[i].options;
  }

  function elementsFrom(data) {
    const els = [];
    for (const n of data.nodes || []) {
      els.push({ group: "nodes", data: {
        id: n.id,
        label: n.name || n._id || (n.labels || [])[0] || "node",
        colour: classColour(n.class),
        size: nodeSize(n.observation_count),
        structural: (n.labels || []).some((l) => STRUCTURAL.has(l)) ? 1 : 0,
        raw: n,
      } });
    }
    for (const e of data.edges || []) {
      els.push({ group: "edges", data: { id: e.id, source: e.source, target: e.target, label: e.rel_type || e.type || "", raw: e } });
    }
    return els;
  }

  function styleSheet() {
    const text = cssVar("--text", "#2d2a26"), muted = cssVar("--text-muted", "#6f6a60");
    const bg = cssVar("--bg", "#faf9f5"), accent = cssVar("--accent", "#6b6fa8");
    return [
      { selector: "node", style: {
        "background-color": "data(colour)", width: "data(size)", height: "data(size)",
        label: "data(label)", color: text, "font-size": 11, "text-valign": "bottom", "text-margin-y": 4,
        "text-wrap": "ellipsis", "text-max-width": 140, "text-outline-color": bg, "text-outline-width": 2,
      } },
      { selector: "node[structural = 1]", style: { shape: "round-rectangle" } },
      { selector: "edge", style: {
        width: 1.4, "line-color": muted, "target-arrow-color": muted, "target-arrow-shape": "triangle",
        "curve-style": "bezier", label: "data(label)", "font-size": 9, color: muted,
        "text-rotation": "autorotate", "text-outline-color": bg, "text-outline-width": 2,
      } },
      { selector: ":selected", style: { "border-width": 3, "border-color": accent, "line-color": accent, "target-arrow-color": accent } },
      { selector: ".faded", style: { opacity: 0.15 } },
    ];
  }

  function failureText(status, body) {
    const detail = body && typeof body.detail === "string" ? body.detail.split("\n")[0] : "";
    if (status === 400) return `Refused: ${detail || "the query is not allowed"}.`;
    if (status === 404) return "This page cannot draw graphs.";
    if (status === 422) return "The graph block is missing its domain or cypher.";
    if (status === 429) return detail || "Too many graph runs; try again in a minute.";
    if (status === 504) return `Timed out: ${detail || "the query took too long"}.`;
    return `Graph could not be drawn${detail ? `: ${detail}` : "."}`;
  }

  async function fetchGraph(spec, extra = {}) {
    const res = await fetch("/api/graph/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ domain: spec.domain, cypher: spec.cypher, ...extra }),
    });
    let body = null;
    try { body = await res.json(); } catch (_e) { /* not JSON: an error page */ }
    if (!res.ok) throw new Error(failureText(res.status, body));
    return body;
  }

  function summaryText(data) {
    const base = `${data.nodes.length} nodes, ${data.edges.length} edges`;
    if (!data.truncated) return base;
    const parts = [];
    if (data.omitted.nodes || data.omitted.edges) parts.push(`${data.omitted.nodes} nodes and ${data.omitted.edges} edges left out (cap ${data.limits.nodes} nodes)`);
    if (data.rows_capped) parts.push(`only the first ${data.limits.rows} rows read`);
    return `${base}. Capped: ${parts.join("; ")}.`;
  }

  function showInspector(panel, raw, kind, opts, ctx) {
    panel.replaceChildren();
    panel.hidden = false;
    const title = kind === "node" ? (raw.name || raw._id || raw.id) : (raw.rel_type || raw.type);
    panel.appendChild(el("div", "agraph-inspector-title", title));
    const dl = el("dl", "agraph-props");
    const head = kind === "node"
      ? [["class", raw.class], ["valid from", raw._valid_from], ["_id", raw._id]]
      : [["type", raw.type], ["meaning", raw.rel_type]];
    const skip = new Set(["name", "_id", "_valid_from", "entity_class", "rel_type"]);
    const rows = head.concat(Object.entries(raw.properties || {}).filter(([k]) => !skip.has(k)));
    for (const [k, v] of rows) {
      if (v === null || v === undefined || v === "") continue;
      dl.append(el("dt", "", k), el("dd", "", typeof v === "string" ? v : JSON.stringify(v)));
    }
    if (raw.properties_omitted) dl.append(el("dt", "", "…"), el("dd", "", `${raw.properties_omitted} more properties not shown`));
    panel.appendChild(dl);
    if (kind === "node" && opts.inspectorActions && opts.inspectorActions.length) {
      const row = el("div", "agraph-actions");
      for (const action of opts.inspectorActions) {
        row.appendChild(window.ArtmindViewer.button(action.label, action.title, () => action.onClick(raw, ctx)));
      }
      panel.appendChild(row);
    }
  }

  function wire(cy, inspector, opts, ctx) {
    cy.on("tap", "node", (evt) => showInspector(inspector, evt.target.data("raw"), "node", opts, { ...ctx, cy, node: evt.target }));
    cy.on("tap", "edge", (evt) => showInspector(inspector, evt.target.data("raw"), "edge", opts, { ...ctx, cy }));
    cy.on("tap", (evt) => { if (evt.target === cy) inspector.hidden = true; });
    cy.on("mouseover", "node", (evt) => cy.elements().not(evt.target.closedNeighborhood()).addClass("faded"));
    cy.on("mouseout", "node", () => cy.elements().removeClass("faded"));
  }

  const slug = (t) => String(t || "").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 60);
  function downloadPng(cy, title) {
    const a = document.createElement("a");
    a.href = cy.png({ full: true, scale: 2, bg: cssVar("--bg", "#ffffff") });
    a.download = `${slug(title) || "graph"}.png`;
    document.body.appendChild(a);
    a.click();
    a.remove();
  }

  function expandGraph(cy, opts, ctx) {
    let big = null;
    const view = window.ArtmindViewer.open({
      label: "Graph",
      buttons: [["fit", "Fit to window", () => big.fit(undefined, 30)]],
      onResize: () => big && big.resize(),
    });
    const canvas = el("div", "agraph-full");
    const inspector = el("div", "agraph-inspector");
    inspector.hidden = true;
    view.overlay.insertBefore(canvas, view.bar);
    view.overlay.insertBefore(inspector, view.bar);
    big = window.cytoscape({ container: canvas, elements: cy.elements().jsons(), style: styleSheet(), layout: { name: "preset" } });
    big.fit(undefined, 30);
    wire(big, inspector, opts, ctx);
  }

  function toolbar(cy, state, opts, ctx) {
    const V = window.ArtmindViewer;
    const bar = el("div", "agraph-toolbar");
    bar.appendChild(V.button("expand", "Open full window", () => expandGraph(cy, opts, ctx)));
    const layoutBtn = V.button(`layout: ${LAYOUTS[0].label}`, "Switch layout", () => {
      state.layout = (state.layout + 1) % LAYOUTS.length;
      layoutBtn.textContent = `layout: ${LAYOUTS[state.layout].label}`;
      cy.layout(layoutOptions(state.layout)).run();
    });
    bar.appendChild(layoutBtn);
    const copy = V.button("copy Cypher", "Copy the Cypher that drew this graph", async () => {
      try { await navigator.clipboard.writeText(state.cypher); copy.textContent = "copied"; }
      catch (_e) { copy.textContent = "failed"; }
      setTimeout(() => (copy.textContent = "copy Cypher"), 1200);
    });
    bar.appendChild(copy);
    bar.appendChild(V.button("PNG", "Download as PNG", () => downloadPng(cy, state.spec.title)));
    if (opts.onEditCypher) bar.appendChild(V.button("edit Cypher", "Open this Cypher in the graph drawer", () => opts.onEditCypher(state.spec, state.cypher)));
    return bar;
  }

  async function mount(host, spec, opts = {}) {
    if (host._agraph) host._agraph.cy.destroy();
    host._agraph = null;
    host.classList.add("agraph");
    host.classList.remove("agraph-failed");
    host.replaceChildren();
    if (spec.title) host.appendChild(el("div", "agraph-title", spec.title));
    const status = el("div", "agraph-note", "Drawing graph…");
    host.appendChild(status);
    const [cytoscape, data] = await Promise.all([load(), fetchGraph(spec)]);
    if (!data.nodes.length) {
      const err = new Error("The query returned no nodes.");
      err.cypher = data.cypher;
      throw err;
    }
    status.textContent = summaryText(data);
    const canvas = el("div", "agraph-canvas");
    const inspector = el("div", "agraph-inspector");
    inspector.hidden = true;
    host.append(canvas, inspector);
    const state = { spec, cypher: data.cypher, layout: 0 };
    const cy = cytoscape({ container: canvas, elements: elementsFrom(data), style: styleSheet(), layout: layoutOptions(0), minZoom: 0.05, maxZoom: 6 });
    const ctx = { state, status };
    wire(cy, inspector, opts, ctx);
    host.appendChild(toolbar(cy, state, opts, ctx));
    host._agraph = { cy, state, status, opts };
    return host._agraph;
  }

  function parseSpec(text) {
    let spec;
    try { spec = JSON.parse(text); } catch (_e) { return { error: "This graph block is not valid JSON, so it was not run." }; }
    if (!spec || typeof spec !== "object" || Array.isArray(spec)) return { error: "A graph block must be a JSON object." };
    const domainOk = (typeof spec.domain === "string" && spec.domain.trim()) || (Array.isArray(spec.domain) && spec.domain.length);
    if (!domainOk || typeof spec.cypher !== "string" || !spec.cypher.trim()) return { error: 'A graph block needs "domain" and "cypher".' };
    return { spec: { domain: spec.domain, cypher: spec.cypher, title: typeof spec.title === "string" ? spec.title : "" } };
  }

  function fallback(host, message, code) {
    if (host._agraph) host._agraph.cy.destroy();
    host._agraph = null;
    host.classList.add("agraph-failed");
    host.replaceChildren();
    host.appendChild(el("div", "agraph-note", message));
    const pre = document.createElement("pre");
    pre.appendChild(el("code", "", code));
    host.appendChild(pre);
    if (typeof addCopyButtons === "function") addCopyButtons(host);
  }

  function blockOptions() {
    if (!window.ArtmindGraphDrawer) return {};
    return { onEditCypher: (spec, cypher) => window.ArtmindGraphDrawer.open({ domain: spec.domain, cypher }) };
  }

  window.renderArtmindGraph = function (scope) {
    for (const code of scope.querySelectorAll("pre > code.language-artmind-graph")) {
      const source = code.textContent;
      const host = el("div", "agraph");
      code.parentElement.replaceWith(host);
      const parsed = parseSpec(source);
      if (parsed.error) { fallback(host, parsed.error, source); continue; }
      mount(host, parsed.spec, blockOptions()).catch((err) => {
        fallback(host, err.message || "Graph could not be drawn.", err.cypher || source);
      });
    }
  };

  window.ArtmindGraph = { mount, parseSpec, fetchGraph, elementsFrom, layoutOptions, NODE_CAP: 500 };

  // Re-colour drawn graphs when the page theme is toggled.
  new MutationObserver(() => {
    for (const host of document.querySelectorAll(".agraph")) {
      const g = host._agraph;
      if (!g) continue;
      g.cy.nodes().forEach((n) => n.data("colour", classColour(n.data("raw").class)));
      g.cy.style(styleSheet());
    }
  }).observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
})();
```

- [ ] **Step 4: Call it wherever mermaid is rendered**

Insert `renderArtmindGraph(<same node>);` directly after each `renderMermaid(...)` call, before `addCopyButtons`:

- `artmind/webui/static/app.js` (`finalizeText`): `renderArtmindGraph(textBlock.el);` after `renderMermaid(textBlock.el);`
- `artmind/webui/static/app.js` (`case "provenance"`): `renderArtmindGraph(node);` after `renderMermaid(node);`
- `artmind/webui/static/conversations.js` (`renderText`): `renderArtmindGraph(node);` after `renderMermaid(node);`
- `artmind/webui/static/views.js` (`renderRendered`): `renderArtmindGraph(target);` after `renderMermaid(target);`

chat-ui (`index.html`) does not load `graph-render.js` until Task 10, and `app.js`/`conversations.js` run there too. So guard each call by writing it as:

```js
  if (window.renderArtmindGraph) renderArtmindGraph(textBlock.el);
```

(with `node` / `target` in the other three). Each line still contains `renderArtmindGraph(` once, which the parity test counts.

- [ ] **Step 5: Load it on the admin pages**

In `artmind/webui/templates/admin.html` and `artmind/webui/templates/dashboard.html`, directly after the `mermaid-render.js` tag, add:

```html
  <script src="{{ static_url('graph-render.js') }}" data-graph-libs="{{ static_url('vendor/cytoscape.min.js') }} {{ static_url('vendor/layout-base.js') }} {{ static_url('vendor/cose-base.js') }} {{ static_url('vendor/cytoscape-fcose.js') }}"></script>
```

- [ ] **Step 6: Add the styles**

Append to `artmind/webui/static/style.css`:

```css
/* ── artmind-graph blocks (graph-render.js) ─────────────────────── */
.agraph { position: relative; margin: 0.8em 0; border: 1px solid var(--border); border-radius: 10px; background: var(--bg-elev); }
.agraph-title { font-weight: 600; font-size: 13px; padding: 8px 12px 0; }
.agraph-note { font-size: 12px; color: var(--text-muted); padding: 6px 12px; }
.agraph-canvas { height: 420px; width: 100%; }
.agraph-toolbar {
  position: absolute; top: 6px; right: 6px; display: flex; gap: 4px;
  opacity: 0; transition: opacity 0.15s;
}
.agraph:hover .agraph-toolbar, .agraph:focus-within .agraph-toolbar { opacity: 1; }
@media (hover: none) { .agraph-toolbar { opacity: 1; } }
.agraph-inspector { border-top: 1px solid var(--border); padding: 8px 12px; font-size: 12px; max-height: 220px; overflow-y: auto; }
.agraph-inspector-title { font-weight: 600; margin-bottom: 4px; overflow-wrap: anywhere; }
.agraph-props { display: grid; grid-template-columns: max-content 1fr; gap: 2px 12px; margin: 0; }
.agraph-props dt { color: var(--text-muted); }
.agraph-props dd { margin: 0; overflow-wrap: anywhere; }
.agraph-actions { display: flex; gap: 6px; margin-top: 8px; }
.agraph.agraph-failed { border: none; background: none; }
.agraph.agraph-failed .agraph-note { padding: 0 0 4px; }
.agraph-full { position: absolute; inset: 0; }
.viewer-overlay .agraph-inspector {
  position: absolute; left: 12px; bottom: 12px; width: 340px; z-index: 1;
  background: var(--bg-elev); border: 1px solid var(--border); border-radius: 10px;
}
```

- [ ] **Step 7: Run the tests**

Run: `uv run --group dev pytest test/test_webui_app.py test/test_graph_tab.py test/test_insights_tabs.py -q`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add artmind/webui/static artmind/webui/templates test/test_webui_app.py
git commit -m "feat(webui): draw artmind-graph blocks with Cytoscape

Live data from /api/graph/run; colour by class, size by observation count,
structural nodes boxed; hover, inspector, layouts, copy Cypher, PNG, and the
shared full-window viewer. An unparseable or failed block stays a code block."
```

---

### Task 8: The `artmind-present` skill and the one routing line

**Files:**
- Create: `artmind/skills/artmind-present/SKILL.md`
- Modify: `artmind/webui/profiles.py:99-126`, `artmind/skills/artmind-query/SKILL.md` (after line 20), `artmind/opencode/agent/artmind.md:11`, `artmind/opencode/agent/artmind-admin.md:11`
- Test: `test/test_present_skill.py` (new), `test/test_webui_profiles.py:53`

- [ ] **Step 1: Write the failing tests**

Create `test/test_present_skill.py`:

```python
"""artmind-present: when to draw what (spec 2026-11-14 §6)."""

import json
import re
from pathlib import Path

from artmind.webui.graph_routes import prepare_cypher
from artmind.webui.profiles import ADMIN_PROFILE, BENCHMARK_PROFILE, QA_PROFILE

ROOT = Path(__file__).resolve().parent.parent
SKILL = ROOT / "artmind" / "skills" / "artmind-present" / "SKILL.md"
QUERY_SKILL = ROOT / "artmind" / "skills" / "artmind-query" / "SKILL.md"
ROUTING_LINE = "Asked to show, draw or graph the answer: after retrieval, load `artmind-present`."


def test_frontmatter_names_the_directory():
    assert re.match(r"---\nname: artmind-present\ndescription: .+\n---\n", SKILL.read_text())


def test_stays_short():
    assert len(SKILL.read_text().splitlines()) <= 100


def test_both_chat_profiles_but_not_benchmarks():
    assert "artmind-present" in QA_PROFILE.skills and "artmind-present" in ADMIN_PROFILE.skills
    assert "artmind-present" not in BENCHMARK_PROFILE.skills


def test_artmind_query_routes_to_it_in_one_line():
    text = QUERY_SKILL.read_text()
    assert text.count("artmind-present") == 1 and ROUTING_LINE in text


def test_the_example_block_is_one_the_endpoint_accepts():
    text = SKILL.read_text()
    block = text.split("```artmind-graph\n", 1)[1].split("\n```", 1)[0]
    spec = json.loads(block)
    assert set(spec) == {"domain", "title", "cypher"}
    prepare_cypher(spec["cypher"], require_domains=True)


def test_every_cli_command_it_names_exists():
    from artmind.cli import cli

    for path in set(re.findall(r"`artmind ((?:query )?(?:graph )?[a-z][a-z0-9-]+)", SKILL.read_text())):
        node = cli
        for part in path.split():
            node = node.commands[part]


def test_personas_name_it():
    for persona in ("artmind.md", "artmind-admin.md"):
        assert "artmind-present" in (ROOT / "artmind" / "opencode" / "agent" / persona).read_text()
```

In `test/test_webui_profiles.py` line 53, change the set to:

```python
    assert set(QA_PROFILE.skills) == {"artmind-query", "artmind-update", "artmind-present"}
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run --group dev pytest test/test_present_skill.py test/test_webui_profiles.py -q`
Expected: FAIL (`SKILL.md` missing).

- [ ] **Step 3: Write the skill**

Create `artmind/skills/artmind-present/SKILL.md`:

````markdown
---
name: artmind-present
description: Show an answer as a picture in the artmind chat UIs — a markdown table, a mermaid diagram, or an interactive artmind-graph block drawn live from the graph. Load it after retrieval when the user asks to show, draw, plot, map or graph something. It says which picture fits and how to write it.
---

# artmind Present

Retrieval comes first: answer with `artmind-query`. Then use this skill to present what retrieval
found. A picture never adds a fact.

## Choose the picture

| The answer is | Use |
|---|---|
| a list, or attributes compared across a few things | a markdown table |
| 5–15 facts already in hand: a short timeline, a process, one relationship chain | a ` ```mermaid ` diagram |
| "how are these connected", "show the network", more than ~15 nodes, or the user wants to explore | an ` ```artmind-graph ` block |

Do not draw figures (counts per year, amounts): there is no chart library. Use a table.

## Mermaid rules

- Put quotes around every label: `A["Dr Rao"] --> B["Cardiology"]`.
- Write `<br/>` for a second line in a label. Never put a raw newline in a label.
- Never leave a parenthesis or a colon outside quotes.
- Draw at most 15 nodes. For more, use an artmind-graph block.
- Use `graph LR` for chains and hierarchies, and `timeline` for dated sequences.

## artmind-graph rules

The block is JSON. The UI runs its Cypher read-only against the live graph and draws the nodes
and relationships that the Cypher returns. The user can drag nodes, zoom, click a node to inspect
it, and copy the Cypher.

```artmind-graph
{
  "domain": "banking",
  "title": "Who the Credit Committee works with",
  "cypher": "MATCH (a:Entity {name: 'Credit Committee'})-[r:RELATES_TO]-(b:Entity) WHERE (b._domain IN $domains OR any(dom IN $domains WHERE b._domain STARTS WITH (dom + '.'))) RETURN a, r, b LIMIT 100"
}
```

- Get the Cypher in one of two ways. Start from `artmind query graph text2cypher "<question>" --domain D --dryRun --compact` and rewrite its `RETURN`. Or write the pattern yourself from artmind-query's `references/graph-model.md`.
- Return whole nodes and relationships (`RETURN a, r, b`). Columns of names or counts draw nothing.
- Bind every relationship that you want drawn (`-[r:RELATES_TO]-`, then return `r`).
- Scope every matched entity with the `$domains` predicate shown above. The UI binds `$domains` to the block's `domain`.
- `$domains` is the only parameter. Write every other value (an `_id`, a name, a date) as a quoted literal. The `--dryRun` output keeps its other values in `parameters`: put them into the Cypher.
- Return bound entities, not paths, unless the path is the point. End with `LIMIT`. The UI stops at 500 nodes and says how many it left out.
- Write read-only Cypher: no `CREATE`, `MERGE`, `SET`, `DELETE`, `CALL`. The UI refuses them.
- Put `domain`, `title` and `cypher` each on one line, and write the Cypher on one line. A raw newline inside a JSON string breaks the block.
- Write one block per answer, unless the user asks for more.

## Honesty

- Keep the answer's caveats (an undercount, an ambiguous name) in the text beside the picture.
- In mermaid, never draw an edge that the results do not hold.
- An artmind-graph block redraws from live data each time it is opened. The answer text carries the claim, and the title says what was asked.
````

- [ ] **Step 4: Add the routing line, the profiles, and the personas**

In `artmind/skills/artmind-query/SKILL.md`, after the line `4. **Answer part by part** — every part from its own evidence; say which part has none.`, add a blank line and then:

```markdown
Asked to show, draw or graph the answer: after retrieval, load `artmind-present`.
```

In `artmind/webui/profiles.py` add `"artmind-present",` after `"artmind-update",` in both `QA_PROFILE.skills` and `ADMIN_PROFILE.skills`.

In `artmind/opencode/agent/artmind.md` line 11, change `artmind-update for adding facts,` to `artmind-update for adding facts, artmind-present for showing an answer as a table, diagram or graph,`.

In `artmind/opencode/agent/artmind-admin.md` line 11, change `artmind-update for adding, retracting, and correcting facts,` to `artmind-update for adding, retracting, and correcting facts, artmind-present for showing an answer as a table, diagram or interactive graph,`.

- [ ] **Step 5: Link the skill into the checkout and run the tests**

```bash
just dev-refresh-skills
uv run --group dev pytest test/test_present_skill.py test/test_webui_profiles.py test/test_webui_help.py test/test_create_view_skill.py test/test_query_skill_commands.py -q
```

Expected: PASS. If a test elsewhere pins the exact `ADMIN_PROFILE.skills` list or the number of help concepts, add `artmind-present` to it. Find such tests with `grep -rn "artmind-build-topic" test`.

- [ ] **Step 6: Commit**

```bash
git add artmind/skills/artmind-present artmind/skills/artmind-query/SKILL.md artmind/webui/profiles.py artmind/opencode/agent test/test_present_skill.py test/test_webui_profiles.py
git commit -m "feat(skills): artmind-present: table, mermaid or artmind-graph

Both chat profiles, not benchmarks. artmind-query gains one routing line."
```

---

### Task 9: Docs

**Files:**
- Modify: `CLAUDE.md` (repo-layout table rows `artmind/webui/`, `artmind/webui/explore_routes.py`; new row after it)

- [ ] **Step 1: Edit CLAUDE.md**

In the `artmind/webui/explore_routes.py` row, replace `The admin console's **Explore → Graph** sub-tab (`static/graph.js`) and the agent console's "View as graph" link under each user message (`static/admin-graph.js`, admin only).` with `The admin console's **Explore → Graph** sub-tab (`static/graph.js`).`

Add this row directly after the explore_routes row:

```markdown
| `artmind/webui/graph_routes.py` | `POST /api/graph/run` (spec `docs/superpowers/specs/2026-11-14-interactive-graph-design.md`): runs an ```` ```artmind-graph ```` fence's `{domain, cypher}` in a `read_session()` with only `$domains` bound (another `$name` is a 400), `validate_read_only` + `_prepare_graph_cypher` (no `CALL`, `LIMIT 100` appended), 15 s timeout, 2000 rows, 500 nodes (`truncated`/`omitted` say what was cut), relationship endpoints the query did not return fetched by element id; nodes trimmed to `id, labels, name, _id, class, observation_count, _valid_from` + 40 capped properties. `mode="admin"` on the admin console; `mode="qa"` (chat-ui) also needs `$domains` and allows 10 runs/minute per client. Not traced; logs domains and length, never the Cypher. Drawn by `static/graph-render.js` (vendored Cytoscape + fcose, `static/vendor/VENDORED.md`, loaded on the first block; full-window view shared with mermaid via `static/viewer.js`). The agent writes the fence by the `artmind-present` skill. |
```

In the `artmind/webui/` row, after `the console pre-fills its input from `/?ask=``, add: `; markdown is passed to `renderMermaid` and `renderArtmindGraph` (`mermaid-render.js`, `graph-render.js`) wherever it is injected`.

- [ ] **Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: CLAUDE.md rows for the graph endpoint and renderer"
```

---

### Task 10: chat-ui support (gated on spec §8 Q1)

**Not done: the user decided on 2026-11-14 that chat-ui draws no graphs (mermaid and tables only). The qa mode was removed from `graph_routes.py`. The steps below are kept as a record.**

**Before starting, ask the user:** spec §8 Q1 asks whether chat-ui may run any read-only, `$domains`-scoped, capped, rate-limited Cypher sent from its browser (v1, no fence signing). If the answer is no, or no answer yet, skip this task: phase 1 ships admin-only, and chat-ui shows the fence as a code block.

**Files:**
- Modify: `artmind/webui/app.py`, `artmind/webui/templates/index.html`
- Test: `test/test_graph_tab.py`, `test/test_webui_app.py`

- [ ] **Step 1: Change the tests**

In `test/test_graph_tab.py` replace `test_graph_run_route_is_on_the_admin_console_only` with:

```python
def test_graph_run_route_is_on_both_uis_and_chat_ui_needs_domains(monkeypatch):
    from artmind.webui import graph_routes

    monkeypatch.setattr(graph_routes, "_available_domains", lambda: ["d"])
    monkeypatch.setattr(graph_routes, "run_graph", lambda *a, **k: {"nodes": [], "edges": [], "truncated": False})
    assert _admin().post("/api/graph/run", json={"domain": "d"}).status_code == 422
    chat = TestClient(create_app(admin_routes=False))
    assert chat.post("/api/graph/run", json={"domain": "d", "cypher": "MATCH (n) RETURN n"}).status_code == 400
    scoped = "MATCH (n) WHERE n._domain IN $domains RETURN n"
    assert chat.post("/api/graph/run", json={"domain": "d", "cypher": scoped}).status_code == 200
```

In `test/test_webui_app.py`, change the parametrisation of `test_admin_pages_load_the_graph_renderer_lazily` to also cover `index.html`. Rename it to `test_pages_load_the_graph_renderer_lazily`:

```python
@pytest.mark.parametrize("template_name, admin, path", [
    ("index.html", False, "/"), ("admin.html", True, "/"), ("dashboard.html", True, "/dashboard"),
])
def test_pages_load_the_graph_renderer_lazily(template_name, admin, path):
    client = TestClient(create_app(registry=SessionRegistry(client_factory=FakeBackend),
                                   template_name=template_name, admin_routes=admin))
    html = client.get(path).text
    assert "/static/graph-render.js?v=" in html
    libs = html.split('data-graph-libs="', 1)[1].split('"', 1)[0].split()
    assert [lib.split("?")[0] for lib in libs] == [f"/static/vendor/{name}" for name in GRAPH_LIBS]
    assert '<script src="/static/vendor/cytoscape' not in html
    assert html.index("/static/viewer.js?v=") < html.index("/static/graph-render.js?v=")
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run --group dev pytest test/test_graph_tab.py test/test_webui_app.py -q -k graph`
Expected: FAIL (chat-ui 404 / no script).

- [ ] **Step 3: Register both modes and load the renderer**

In `artmind/webui/app.py` remove `register_graph_routes(app, mode="admin")` from the `if admin_routes:` block. Add this line directly above the block:

```python
    register_graph_routes(app, mode="admin" if admin_routes else "qa")
```

In `artmind/webui/templates/index.html`, directly after the `mermaid-render.js` tag, add the same `graph-render.js` tag as in Task 7, Step 5.

- [ ] **Step 4: Run the tests**

Run: `just dev-test`
Expected: PASS (about 5,000 tests).

- [ ] **Step 5: Commit**

```bash
git add artmind/webui/app.py artmind/webui/templates/index.html test/test_graph_tab.py test/test_webui_app.py
git commit -m "feat(chat-ui): draw artmind-graph blocks

qa mode: \$domains required, 10 runs/minute per client, same caps and timeout.
Fences are not signed (spec §8 Q1, decided <date> by the user)."
```

---

### Task 11: Phase 1 live check on brain01

There is no JS test harness for the web UI, so the browser checks here are the proof that the canvas works. Do not ask the user to check by hand.

- [ ] **Step 1: Run the full suite**

Run: `just dev-test`
Expected: PASS.

- [ ] **Step 2: Prepare brain01 and a launch entry**

```bash
cd ~/artmind_vaults/brain01 && artmind init
```

This links `artmind-present` into the vault's `.claude/skills/`. Check with `ls ~/artmind_vaults/brain01/.claude/skills/`.

Add this configuration to `.claude/launch.json` (gitignored). Unsetting `ANTHROPIC_BASE_URL` stops Claude Code's endpoint from taking over the agent's `text2cypher` LLM calls (CLAUDE.md §5). `ARTMIND_NO_PROXY=1` keeps a stale `serve` out of the loop:

```json
{
  "name": "admin-brain01",
  "runtimeExecutable": "sh",
  "runtimeArgs": ["-c", "cd \"$HOME/artmind_vaults/brain01\" && exec env -u ANTHROPIC_BASE_URL ARTMIND_NO_PROXY=1 artmind admin-ui --port 8382"],
  "port": 8382
}
```

brain01's graph is a local Neo4j on bolt port 7689. If `preview_logs` shows a connection error, ask the user to start that Neo4j. Do not probe ports.

- [ ] **Step 3: Draw the trace's question**

`preview_start admin-brain01`. Fill `#prompt` with `how many doctors have treated mummy, show me as a graph` and submit with `preview_click` on `#send`. Wait for the turn to finish: poll `preview_snapshot` until the stop icon is gone. Then:

- `preview_eval`: `(() => { const h = document.querySelector('.agraph'); return h && h._agraph ? { nodes: h._agraph.cy.nodes().length, edges: h._agraph.cy.edges().length, cypher: h._agraph.state.cypher } : document.querySelector('.agraph')?.textContent })()` returns a node count above 1.
- Drag check: `preview_eval` moves a node with `h._agraph.cy.nodes()[0].position({x: 10, y: 10})` and reads the position back; also `preview_click` on `.agraph-toolbar .viewer-btn` (expand) and screenshot the overlay.
- Compare the drawn Cypher with the trace. Run `artmind traces list --compact` in the vault, then `artmind traces show <trace_id> --compact`. The fence's Cypher should match the pattern of the agent's `text2cypher` Cypher (same match, `RETURN` rewritten to whole nodes). Also check the trace shows `Skill:artmind-present`.
- `preview_console_logs level=error`: no errors.
- `preview_screenshot`: keep it as the proof.

- [ ] **Step 4: Reload restores it**

`preview_eval: location.reload()`, then confirm with `preview_snapshot`/`preview_eval` that the conversation (`/?c=<id>`) redraws `.agraph` with nodes.

- [ ] **Step 5: Refusals**

```bash
curl -s -X POST http://127.0.0.1:8382/api/graph/run -H 'Content-Type: application/json' \
  -d '{"domain":"personal_journal","cypher":"MATCH (n) DETACH DELETE n"}'
```

```bash
curl -s -X POST http://127.0.0.1:8382/api/graph/run -H 'Content-Type: application/json' \
  -d '{"domain":"personal_journal","cypher":"CALL db.labels() YIELD label RETURN label"}'
```

Expected: both `{"detail": "..."}` with HTTP 400 (add `-w '%{http_code}'` to see the code). The first names the write operation; the second names `CALL`.

- [ ] **Step 6: Dark mode**

Use `preview_click` on `#theme-toggle`, then `preview_screenshot`. Node colours change and labels stay readable.

- [ ] **Step 7: chat-ui (only if Task 10 was done)**

Add a `chat-brain01` launch entry like the one above (`artmind chat-ui --port 8383`) and ask the same question. Expected: the same kind of block is drawn. Then send 11 quick `curl` runs with a scoped Cypher and confirm the 11th returns 429.

- [ ] **Step 8: Record and stop**

`preview_stop` both servers. Note the results (node counts, the Cypher comparison, screenshots taken) in the PR description, not in the repo: the conversation text is private brain01 data.

---

# Phase 2 — drawer, re-hosted tab, neighbour expansion

### Task 12: `expand` on the endpoint (server-owned one-hop Cypher)

**Files:**
- Modify: `artmind/webui/graph_routes.py`
- Test: `test/test_graph_routes.py`

- [ ] **Step 1: Write the failing tests**

Append to `test/test_graph_routes.py`:

```python
def test_expand_runs_the_fixed_one_hop_query_with_the_node_id(ran):
    r = _client("qa").post("/api/graph/run", json={"domain": "personal_journal", "expand": "4:abc:12"})
    assert r.status_code == 200
    cypher, domains, extra = ran[0]
    assert cypher == graph_routes.EXPAND_CYPHER and extra == {"id": "4:abc:12"}
    assert "$domains" in cypher and "elementId(n) = $id" in cypher


@pytest.mark.parametrize("body", [
    {"domain": "personal_journal"},
    {"domain": "personal_journal", "cypher": SCOPED, "expand": "4:abc:12"},
    {"domain": "personal_journal", "expand": "  "},
])
def test_exactly_one_of_cypher_and_expand(ran, body):
    assert _client("admin").post("/api/graph/run", json=body).status_code == 422
```

In `test_malformed_requests_are_422`, change the parametrisation to `[{"cypher": SCOPED}, {"domain": "d", "cypher": "  "}]`, because `{"domain": "d"}` is now covered above.

- [ ] **Step 2: Run them to see them fail**

Run: `uv run --group dev pytest test/test_graph_routes.py -q`
Expected: FAIL.

- [ ] **Step 3: Implement**

In `artmind/webui/graph_routes.py` add `model_validator` to the pydantic import and replace `GraphRunRequest` with:

```python
class GraphRunRequest(BaseModel):
    domain: str | list[str]
    cypher: str | None = None
    expand: str | None = None  # a node's element id: draw its one-hop neighbours

    @field_validator("cypher")
    @classmethod
    def _cypher(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value.strip():
            raise ValueError("cypher must not be empty")
        if len(value) > MAX_CYPHER_CHARS:
            raise ValueError(f"cypher is longer than {MAX_CYPHER_CHARS} characters")
        return value

    @field_validator("expand")
    @classmethod
    def _expand(cls, value: str | None) -> str | None:
        if value is not None and (not value.strip() or len(value) > 200):
            raise ValueError("expand must be a node id")
        return value

    @model_validator(mode="after")
    def _one_of(self):
        if (self.cypher is None) == (self.expand is None):
            raise ValueError("send exactly one of cypher and expand")
        return self
```

Below `ENDPOINT_CYPHER` add:

```python
def _expand_cypher() -> str:
    from artmind.graph_query import domain_predicate

    return (
        "MATCH (n) WHERE elementId(n) = $id\n"
        "MATCH (n)-[r]-(m)\n"
        f"WHERE {domain_predicate('m')}\n"
        "RETURN n, r, m\n"
        "LIMIT 100"
    )


EXPAND_CYPHER = _expand_cypher()
```

In `api_graph_run`, replace the `try: domains = ... cypher = prepare_cypher(...)` block and the `run_sync` call with:

```python
        try:
            domains = resolve_domains(payload.domain)
            if payload.expand is not None:
                cypher, extra = EXPAND_CYPHER, {"id": payload.expand}
            else:
                cypher, extra = prepare_cypher(payload.cypher, require_domains=mode == "qa"), None
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        try:
            result = await anyio.to_thread.run_sync(run_graph, cypher, domains, extra)
        except (Neo4jError, DriverError) as exc:
            raise _db_error(exc) from exc
```

Update `test_admin_run_returns_the_graph_and_the_cypher_it_ran`: the expected `ran` entry stays `(SCOPED + "\nLIMIT 100", ["personal_journal"], None)`, which already matches.

- [ ] **Step 4: Run the tests**

Run: `uv run --group dev pytest test/test_graph_routes.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add artmind/webui/graph_routes.py test/test_graph_routes.py
git commit -m "feat(webui): graph run can expand a node's one-hop neighbours

The server owns the Cypher (elementId + \$domains on the neighbour); the
browser sends only the node id."
```

---

### Task 13: Neighbour expansion in the renderer

**Files:**
- Modify: `artmind/webui/static/graph-render.js`
- Test: `test/test_webui_app.py`

- [ ] **Step 1: Write the failing test**

Append to `test/test_webui_app.py`:

```python
def test_graph_renderer_expands_neighbours_through_the_endpoint():
    from artmind.webui.app import WEBUI_DIR

    source = (WEBUI_DIR / "static" / "graph-render.js").read_text()
    assert '"dbltap"' in source and "expand: node.id()" in source and '"Neighbours"' in source
```

- [ ] **Step 2: Run it to see it fail**

Run: `uv run --group dev pytest test/test_webui_app.py -q -k neighbours`
Expected: FAIL.

- [ ] **Step 3: Implement**

In `artmind/webui/static/graph-render.js` add this function above `function wire`:

```js
  async function expandNode(cy, ctx, node) {
    const cap = window.ArtmindGraph.NODE_CAP;
    const room = cap - cy.nodes().length;
    if (room <= 0) { ctx.status.textContent = `At the ${cap}-node cap; nothing added.`; return; }
    ctx.status.textContent = `Expanding ${node.data("label")}…`;
    try {
      const data = await fetchGraph({ domain: ctx.state.spec.domain }, { expand: node.id() });
      const freshNodes = data.nodes.filter((n) => cy.getElementById(n.id).empty());
      const keptNodes = freshNodes.slice(0, room);
      const ids = new Set(cy.nodes().map((n) => n.id()).concat(keptNodes.map((n) => n.id)));
      const edges = data.edges.filter((e) => ids.has(e.source) && ids.has(e.target) && cy.getElementById(e.id).empty());
      const added = cy.add(elementsFrom({ nodes: keptNodes, edges }));
      added.nodes().position({ ...node.position() });
      cy.layout({ ...layoutOptions(ctx.state.layout), randomize: false }).run();
      const left = freshNodes.length - keptNodes.length;
      ctx.status.textContent = `${keptNodes.length} nodes added around ${node.data("label")}` + (left ? `; ${left} left out (cap ${cap} nodes).` : ".");
    } catch (err) {
      ctx.status.textContent = err.message;
    }
  }
```

In `wire`, add:

```js
    cy.on("dbltap", "node", (evt) => expandNode(cy, ctx, evt.target));
```

In `showInspector`, for nodes add a built-in **Neighbours** button before the `opts.inspectorActions` loop. Replace the `if (kind === "node" && opts.inspectorActions ...)` block with:

```js
    if (kind === "node" && ctx.node) {
      const row = el("div", "agraph-actions");
      row.appendChild(window.ArtmindViewer.button("Neighbours", "Add this node's neighbours (or double-click it)", () => expandNode(ctx.cy, ctx, ctx.node)));
      for (const action of opts.inspectorActions || []) {
        row.appendChild(window.ArtmindViewer.button(action.label, action.title, () => action.onClick(raw, ctx)));
      }
      panel.appendChild(row);
    }
```

The full-window copy gets its own status line. In `expandGraph`, create `const note = el("div", "agraph-note agraph-full-note");`, insert it before `view.bar`, and call `wire(big, inspector, opts, { ...ctx, status: note });`. Add to `style.css`: `.agraph-full-note { position: absolute; left: 12px; top: 12px; z-index: 1; }`.

- [ ] **Step 4: Run the tests**

Run: `uv run --group dev pytest test/test_webui_app.py -q`
Expected: PASS.

- [ ] **Step 5: Check it live**

Using the Task 11 launch entry, draw a graph, then `preview_eval` a double tap: `(() => { const g = document.querySelector('.agraph')._agraph; const n0 = g.cy.nodes().length; g.cy.nodes()[0].emit('dbltap'); return n0 })()`. Wait about two seconds, then read `g.cy.nodes().length` and `.agraph-note` text. Expected: more nodes, or a note saying none were added.

- [ ] **Step 6: Commit**

```bash
git add artmind/webui/static/graph-render.js artmind/webui/static/style.css test/test_webui_app.py
git commit -m "feat(webui): expand a graph node's neighbours (double-click or Neighbours)"
```

---

### Task 14: Explore routes: Cypher to run, and links that take domains

**Files:**
- Modify: `artmind/webui/explore_routes.py`
- Test: `test/test_explore_routes.py`

- [ ] **Step 1: Write the failing tests**

Append to `test/test_explore_routes.py`:

```python
def test_cypher_response_carries_run_cypher_with_only_domains_left(client, monkeypatch):
    monkeypatch.setattr(explore_routes, "_generate", lambda q, d: {
        "cypher": "MATCH (a:Entity {name: $name}) WHERE a._domain IN $domains RETURN a",
        "parameters": {"domains": d, "name": "O'Neil"},
    })
    body = client.post("/api/explore/cypher", json={"question": "q", "domains": ["d"]}).json()
    assert body["run_cypher"] == "MATCH (a:Entity {name: 'O\\'Neil'}) WHERE a._domain IN $domains RETURN a"


def test_link_inlines_domains_when_given(client):
    r = client.post("/api/explore/cypher/link",
                    json={"cypher": "MATCH (a) WHERE a._domain IN $domains RETURN a", "domains": ["d"]})
    assert r.status_code == 200 and "['d']" in r.json()["standalone_cypher"]


def test_link_without_domains_still_refuses_parameters(client):
    r = client.post("/api/explore/cypher/link", json={"cypher": "MATCH (a) WHERE a._domain IN $domains RETURN a"})
    assert r.status_code == 400
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run --group dev pytest test/test_explore_routes.py -q`
Expected: FAIL (`KeyError: 'run_cypher'`, and the link returns 400).

- [ ] **Step 3: Implement**

In `artmind/webui/explore_routes.py`:

Add `domains: list[str] | None = None` to `LinkRequest`.

Add this helper below `_package`:

```python
def _inline_except_domains(cypher: str, parameters: dict) -> str:
    """Cypher for /api/graph/run: every parameter inlined except `$domains`,
    which the graph endpoint binds itself."""
    from artmind.text2cypher import _PARAM_RE, cypher_literal

    def sub(match):
        name = match.group(1)
        if name == "domains":
            return match.group(0)
        if name not in parameters:
            raise ValueError(f"Cypher references ${name} but no value was generated for it")
        return cypher_literal(parameters[name])

    return _PARAM_RE.sub(sub, cypher)
```

In `_package`, add `"run_cypher": _inline_except_domains(cypher, parameters),` to the returned dict.

In `api_explore_cypher_link`, replace `standalone = inline_parameters(payload.cypher, {})` with:

```python
            params = {"domains": normalize_domains(payload.domains)} if payload.domains else {}
            standalone = inline_parameters(payload.cypher, params)
```

and add `from artmind.graph_query import normalize_domains` to that function's imports.

Update the module docstring: append the sentence `It also returns `run_cypher` (everything but `$domains` inlined) for /api/graph/run.`

- [ ] **Step 4: Run the tests**

Run: `uv run --group dev pytest test/test_explore_routes.py test/test_graph_tab.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add artmind/webui/explore_routes.py test/test_explore_routes.py
git commit -m "feat(webui): explore routes give Cypher for /api/graph/run and domain-aware links"
```

---

### Task 15: The admin graph drawer

**Files:**
- Create: `artmind/webui/static/graph-drawer.js`
- Modify: `artmind/webui/templates/admin.html`, `artmind/webui/static/style.css:323`
- Test: `test/test_graph_tab.py`

- [ ] **Step 1: Write the failing tests**

Append to `test/test_graph_tab.py`:

```python
def test_admin_console_has_the_graph_drawer():
    html = TestClient(create_app(template_name="admin.html", admin_routes=True)).get("/").text
    for el_id in ("graph-drawer-toggle", "graph-drawer", "gd-domain", "gd-question", "gd-generate",
                  "gd-cypher", "gd-run", "gd-browser", "gd-status", "gd-canvas"):
        assert f'id="{el_id}"' in html, el_id
    assert html.index("/static/graph-render.js?v=") < html.index("/static/graph-drawer.js?v=") < html.index("/static/app.js?v=")
    assert "graph-drawer.js" not in TestClient(create_app(admin_routes=False)).get("/").text


def test_graph_drawer_never_sends_a_chat_turn_and_never_uses_innerhtml():
    source = _admin().get("/static/graph-drawer.js").text
    assert "innerHTML" not in source
    assert "/api/chat" not in source and ".submit(" not in source and "requestSubmit" not in source
    assert "window.ArtmindGraphDrawer" in source and '"/api/explore/cypher"' in source
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run --group dev pytest test/test_graph_tab.py -q`
Expected: FAIL.

- [ ] **Step 3: Add the markup**

In `artmind/webui/templates/admin.html`, add this button in the topbar directly before `<button id="help-toggle"`:

```html
      <button id="graph-drawer-toggle" class="icon-btn" title="Graph" aria-label="Toggle graph drawer">
        <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2">
          <circle cx="5" cy="6" r="2.5"></circle><circle cx="19" cy="6" r="2.5"></circle><circle cx="12" cy="18" r="2.5"></circle>
          <path d="M7.2 7.2 10.8 16M16.8 7.2 13.2 16M7.5 6h9"></path>
        </svg>
      </button>
```

and this aside directly after the help drawer's `</aside>`:

```html
  <aside id="graph-drawer" class="drawer graph-drawer" aria-hidden="true">
    <div class="drawer-head">
      <span class="drawer-title">Graph</span>
      <button id="graph-drawer-close" class="icon-btn" aria-label="Close graph" tabindex="-1">
        <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2">
          <path d="M6 6l12 12M18 6L6 18"></path>
        </svg>
      </button>
    </div>
    <div class="graph-drawer-body">
      <label class="gd-label">Domain <select id="gd-domain"></select></label>
      <label class="gd-label">Question <textarea id="gd-question" rows="2" placeholder="e.g. who treated whom, and when?"></textarea></label>
      <div class="gd-row"><button id="gd-generate" type="button" class="viewer-btn">Generate Cypher</button></div>
      <label class="gd-label">Cypher (read-only; only $domains may be a parameter)
        <textarea id="gd-cypher" rows="6" spellcheck="false"></textarea></label>
      <div class="gd-row">
        <button id="gd-run" type="button" class="viewer-btn gd-primary">Run</button>
        <button id="gd-browser" type="button" class="viewer-btn">Open in Neo4j Browser</button>
      </div>
      <p id="gd-status" class="gd-status"></p>
      <div id="gd-canvas"></div>
    </div>
  </aside>
```

After the `graph-render.js` script tag, add:

```html
  <script src="{{ static_url('graph-drawer.js') }}"></script>
```

- [ ] **Step 4: Add the styles**

In `artmind/webui/static/style.css`, change `body.drawer-open .drawer:not(#help-drawer) { transform: translateX(0); }` to:

```css
body.drawer-open .drawer:not(#help-drawer):not(#graph-drawer) { transform: translateX(0); }
```

and append:

```css
/* ── admin graph drawer (graph-drawer.js) ───────────────────────── */
#graph-drawer { width: 600px; }
#graph-drawer.open { transform: translateX(0); }
@media (min-width: 1400px) {
  body.graph-open { padding-right: 600px; }
  body.graph-open .composer-wrap { width: calc(100% - 600px); }
}
.graph-drawer-body { overflow-y: auto; padding: 12px 16px; flex: 1; display: flex; flex-direction: column; gap: 10px; }
.gd-label { display: flex; flex-direction: column; gap: 4px; font-size: 12px; color: var(--text-muted); }
.gd-label select, .gd-label textarea {
  font: inherit; font-size: 13px; color: var(--text); background: var(--bg-inset);
  border: 1px solid var(--border); border-radius: 8px; padding: 6px 8px;
}
#gd-cypher { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; }
.gd-row { display: flex; gap: 6px; }
.gd-primary { color: var(--accent-contrast); background: var(--accent); border-color: var(--accent); }
.gd-status { font-size: 12px; color: var(--text-muted); margin: 0; min-height: 1em; }
#gd-canvas .agraph { margin: 0; }
```

- [ ] **Step 5: Write `graph-drawer.js`**

Create `artmind/webui/static/graph-drawer.js`:

```js
"use strict";

// Admin console graph drawer (spec 2026-11-14 §4): domain -> question -> Generate (the existing
// /api/explore/cypher) -> editable Cypher -> Run (/api/graph/run through ArtmindGraph.mount) ->
// canvas -> inspector. It stays open while the conversation goes on. "Ask about this" puts a
// question in the chat input and never sends it. Text goes through textContent only.
// window.ArtmindGraphDrawer.open({domain, cypher}) is what a chat block's "edit Cypher" calls.
(function () {
  const drawer = document.getElementById("graph-drawer");
  if (!drawer) return;
  const LS_KEY = "artmind-graph-domain"; // shared with Explore -> Graph
  const $ = (id) => document.getElementById(id);
  const domainEl = $("gd-domain"), questionEl = $("gd-question"), cypherEl = $("gd-cypher");
  const statusEl = $("gd-status"), canvasEl = $("gd-canvas");
  const generateEl = $("gd-generate"), runEl = $("gd-run"), browserEl = $("gd-browser");
  let domainsLoaded = null;

  async function call(path, body) {
    const init = body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
    const res = await fetch(path, init);
    let data = null;
    try { data = await res.json(); } catch (_e) { /* not JSON */ }
    if (!res.ok) throw new Error((data && typeof data.detail === "string" && data.detail) || `HTTP ${res.status}`);
    return data;
  }
  const lsGet = () => { try { return localStorage.getItem(LS_KEY) || ""; } catch (_e) { return ""; } };
  const lsSet = (v) => { try { localStorage.setItem(LS_KEY, v); } catch (_e) { /* storage unavailable */ } };
  const say = (text) => { statusEl.textContent = text; };

  function loadDomains() {
    if (!domainsLoaded) {
      domainsLoaded = call("/api/explore/domains").then((domains) => {
        domainEl.replaceChildren();
        for (const d of domains) {
          const opt = document.createElement("option");
          opt.value = d.name;
          opt.textContent = d.entities ? `${d.name} (${d.entities} entities)` : `${d.name} (empty)`;
          domainEl.appendChild(opt);
        }
        const saved = domains.find((d) => d.name === lsGet());
        if (saved && saved.entities) domainEl.value = saved.name;
      }).catch((err) => { domainsLoaded = null; throw err; });
    }
    return domainsLoaded;
  }

  function pick(domain) {
    const name = Array.isArray(domain) ? domain[0] : domain;
    if (name && [...domainEl.options].some((o) => o.value === name)) domainEl.value = name;
  }

  function setOpen(open) {
    drawer.classList.toggle("open", open);
    drawer.setAttribute("aria-hidden", String(!open));
    document.body.classList.toggle("graph-open", open);
    if (open) loadDomains().catch((err) => say(`Failed to load domains: ${err.message}`));
  }

  function fillPrompt(text) {
    const prompt = document.getElementById("prompt");
    if (!prompt) return;
    prompt.value = text;
    prompt.dispatchEvent(new Event("input", { bubbles: true }));
    prompt.focus();
  }
  const askAction = {
    label: "Ask about this",
    title: "Put a question about this node in the chat box (it is not sent)",
    onClick: (raw) => fillPrompt(`tell me about ${raw.name || raw._id || "this"}`),
  };

  async function generate() {
    const question = questionEl.value.trim();
    if (!question || !domainEl.value) { say("Pick a domain and type a question."); return; }
    generateEl.disabled = true;
    say("Generating Cypher…");
    try {
      const data = await call("/api/explore/cypher", { question, domains: [domainEl.value] });
      cypherEl.value = data.run_cypher;
      const warnings = (data.warnings || []).join(" ");
      say(warnings ? `Warning: ${warnings} Review the Cypher, then Run.` : "Review the Cypher, then Run.");
    } catch (err) {
      say(`Could not generate Cypher: ${err.message}`);
    } finally {
      generateEl.disabled = false;
    }
  }

  async function run() {
    const cypher = cypherEl.value.trim();
    if (!cypher || !domainEl.value) { say("Pick a domain and write or generate Cypher."); return; }
    lsSet(domainEl.value);
    runEl.disabled = true;
    say("");
    try {
      await window.ArtmindGraph.mount(canvasEl, { domain: domainEl.value, cypher }, { inspectorActions: [askAction] });
    } catch (err) {
      canvasEl.replaceChildren();
      say(err.message || "Graph could not be drawn.");
    } finally {
      runEl.disabled = false;
    }
  }

  async function openBrowser() {
    try {
      const data = await call("/api/explore/cypher/link", { cypher: cypherEl.value, domains: [domainEl.value] });
      if (!data.browser_url) { say("No Neo4j Browser configured for this vault (ARTMIND_KG_NEO4J_BROWSER_URL)."); return; }
      // noopener: Neo4j Browser must not get a handle on this admin page.
      window.open(data.browser_url, "_blank", "noopener,noreferrer");
      say("Opened Neo4j Browser. Press Run there; log in if it asks.");
    } catch (err) {
      say(`Not opened: ${err.message}`);
    }
  }

  async function open(spec = {}) {
    setOpen(true);
    try { await loadDomains(); } catch (_e) { return; }
    pick(spec.domain);
    if (spec.cypher) { cypherEl.value = spec.cypher; await run(); }
  }

  $("graph-drawer-toggle").addEventListener("click", () => setOpen(!drawer.classList.contains("open")));
  $("graph-drawer-close").addEventListener("click", () => setOpen(false));
  generateEl.addEventListener("click", generate);
  runEl.addEventListener("click", run);
  browserEl.addEventListener("click", openBrowser);
  domainEl.addEventListener("change", () => lsSet(domainEl.value));

  window.ArtmindGraphDrawer = { open };
})();
```

- [ ] **Step 6: Run the tests**

Run: `uv run --group dev pytest test/test_graph_tab.py test/test_webui_app.py -q`
Expected: PASS.

- [ ] **Step 7: Check it live**

Restart `admin-brain01` (a running UI serves stale code). Then:

1. `preview_click #graph-drawer-toggle`. The drawer opens and the domain list fills (`preview_snapshot`).
2. Fill `#gd-question`, click `#gd-generate`, and wait for `#gd-cypher` to fill. Click `#gd-run`. `#gd-canvas .agraph` holds nodes (`preview_eval`).
3. Tap a node (`preview_eval`: `document.querySelector('#gd-canvas .agraph')._agraph.cy.nodes()[0].emit('tap')`), then `preview_click` the **Ask about this** button. `#prompt` now holds `tell me about …`. Check no new `/api/chat` request was sent with `preview_network`.
4. In the chat, open a conversation with a drawn block and click its **edit Cypher**. The drawer opens with that Cypher and draws it.
5. Send a chat turn with the drawer open: it stays open.
6. `preview_console_logs level=error`: clean. Take a `preview_screenshot`.

- [ ] **Step 8: Commit**

```bash
git add artmind/webui/static/graph-drawer.js artmind/webui/templates/admin.html artmind/webui/static/style.css test/test_graph_tab.py
git commit -m "feat(admin-ui): graph drawer: question -> Cypher -> Run -> canvas -> inspector

Stays open across turns; chat blocks' edit Cypher opens it; Ask about this
fills the chat input without sending; Neo4j Browser stays a secondary action."
```

---

### Task 16: Re-host Explore → Graph on the same component

**Files:**
- Modify: `artmind/webui/templates/dashboard.html:317-345`, `artmind/webui/static/graph.js`
- Test: `test/test_graph_tab.py`

- [ ] **Step 1: Change the test**

In `test/test_graph_tab.py`, in `test_dashboard_has_graph_subtab_and_script`, extend the id tuple with `"graph-run-btn", "graph-canvas"` and add:

```python
    assert html.index("/static/graph-render.js?v=") < html.index("/static/graph.js?v=")
```

- [ ] **Step 2: Run it to see it fail**

Run: `uv run --group dev pytest test/test_graph_tab.py -q`
Expected: FAIL (`graph-run-btn` missing).

- [ ] **Step 3: Markup**

In `artmind/webui/templates/dashboard.html`, inside `#graph-panel`, change the hint to:

```html
      <p class="dash-hint">Ask a question in plain words. It becomes read-only Cypher you can edit; Run draws it here. Neo4j Browser stays available.</p>
```

and replace the `<div class="dash-form">` that holds `graph-open-btn` and `graph-copy-btn` with:

```html
        <div class="dash-form">
          <button id="graph-run-btn" type="button" class="btn-primary">Run</button>
          <button id="graph-open-btn" type="button" class="btn-secondary">Open in Neo4j Browser</button>
          <button id="graph-copy-btn" type="button" class="btn-secondary">Copy Cypher</button>
        </div>
      </div>
      <div id="graph-canvas"></div>
```

(the second `</div>` closes `#graph-result`, which the old markup closed right after the buttons).

- [ ] **Step 4: Script**

In `artmind/webui/static/graph.js`:

1. Add `const graphRunEl = document.getElementById("graph-run-btn");` and `const graphCanvasEl = document.getElementById("graph-canvas");` with the other element lookups.
2. In `graphGenerate`, change `graphCypherEl.value = data.standalone_cypher;` to `graphCypherEl.value = data.run_cypher;`. Change the success note to `"Review or edit the Cypher, then Run to draw it here."` and keep the no-Browser note's second sentence.
3. In `graphOpen`, change the request body to `JSON.stringify({ cypher: graphCypherEl.value, domains: [graphDomainEl.value] })`.
4. Add:

```js
async function graphRun() {
  const domain = graphDomainEl.value;
  if (!domain || !graphCypherEl.value.trim()) {
    graphSetStatus("Pick a domain and generate or write Cypher.");
    return;
  }
  graphRunEl.disabled = true;
  graphSetStatus("");
  try {
    await window.ArtmindGraph.mount(graphCanvasEl, { domain, cypher: graphCypherEl.value });
  } catch (err) {
    graphCanvasEl.replaceChildren();
    graphSetStatus(err.message || "Graph could not be drawn.");
  } finally {
    graphRunEl.disabled = false;
  }
}

graphRunEl.addEventListener("click", graphRun);
```

5. Update the header comment: replace `Nothing runs here: the operator edits the Cypher, then opens Neo4j Browser (which pre-fills its editor) and runs it.` with `Run draws the Cypher here (graph-render.js, /api/graph/run); Neo4j Browser stays a secondary action.`

- [ ] **Step 5: Load the renderer on the dashboard**

`dashboard.html` already loads `graph-render.js` after `mermaid-render.js` (Task 7) and `graph.js` comes later. Confirm with `grep -n "graph-render\|graph.js" artmind/webui/templates/dashboard.html`.

- [ ] **Step 6: Run the tests and check live**

Run: `uv run --group dev pytest test/test_graph_tab.py test/test_explore_routes.py -q`
Expected: PASS.

Live: open `/dashboard?tab=graph&q=who%20treated%20mummy` on `admin-brain01`. The question is pre-filled. Generate, then Run: `#graph-canvas .agraph` has nodes. **Open in Neo4j Browser** still opens a link, or reports that none is configured. Take a `preview_screenshot`.

- [ ] **Step 7: Commit**

```bash
git add artmind/webui/templates/dashboard.html artmind/webui/static/graph.js test/test_graph_tab.py
git commit -m "feat(admin-ui): Explore -> Graph draws in place; Browser link is secondary"
```

---

### Task 17: Phase 2 docs and full check

- [ ] **Step 1: CLAUDE.md**

In the `artmind/webui/graph_routes.py` row, add after `fetched by element id;`: `` `expand: <elementId>` instead of `cypher` runs the server's one-hop `EXPAND_CYPHER` (neighbours `$domains`-scoped);``. At the end of the row add: ``Admin: `static/graph-drawer.js` (topbar drawer; chat blocks' "edit Cypher" opens it; "Ask about this" fills the input, never sends) and Explore → Graph both mount the same component; `/api/explore/cypher` returns `run_cypher` (all but `$domains` inlined) and `/api/explore/cypher/link` takes `domains`.``

In the `artmind/webui/explore_routes.py` row, replace `Nothing is executed server-side: the operator presses Run in Neo4j Browser.` with `The tab's Run draws in place through `/api/graph/run`; Neo4j Browser is the secondary action.`

- [ ] **Step 2: Full suite**

Run: `just dev-test`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: CLAUDE.md for the graph drawer and re-hosted Explore -> Graph"
```

---

## Self-review (done while writing)

- **Spec coverage.** §1 pieces and removal: Tasks 1, 4, 7, 8, 15. §2 endpoint: Tasks 2–4 (known domain, `$domains` only, READ_ACCESS, read-only and CALL checks, `LIMIT 100`, 15 s, 2000 rows, `serialize_value`, endpoint fetch, 500-node cap with counts, trimmed payload, modes, rate limit, no trace, log without Cypher); chat-ui in Task 10. §3 renderer: Tasks 5–7; expansion and edit Cypher in Tasks 13 and 15, as §7 puts them in phase 2. §4 drawer: Tasks 14–16. §5 needs no code; Task 11 checks reload and the `Skill:artmind-present` trace. §6 skill: Task 8. §7 exits: Tasks 11 and 15–16. §8 Q1: Task 10 gate; Q2: Task 5 `VENDORED.md`; Q3: not needed.
- **Names used across tasks:** `resolve_domains`, `prepare_cypher`, `run_graph(cypher, domains, extra_params)`, `GraphCollector`, `ENDPOINT_CYPHER`, `EXPAND_CYPHER`, `RateLimiter`, `register_graph_routes(app, *, mode)`; JS `ArtmindViewer.{button, open}`, `ArtmindGraph.{mount, parseSpec, fetchGraph, elementsFrom, layoutOptions, NODE_CAP}`, `renderArtmindGraph`, `ArtmindGraphDrawer.open`; response keys `nodes, edges, truncated, omitted{nodes,edges}, rows_read, rows_capped, limits, cypher, domains`; explore `run_cypher`.
