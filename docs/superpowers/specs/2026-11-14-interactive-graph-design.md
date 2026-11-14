# Interactive graph in the chat UIs: an `artmind-graph` block, a Cypher endpoint, a presentation skill

Status: phases 1 and 2 built 2026-11-14 on branch `interactive-graph`, admin console only. **Decided 2026-11-14: chat-ui draws no graphs** (it keeps mermaid and tables), so the qa mode in §2 and chat-ui in §7 are not built; see §8 Q1. Design as first written 2026-11-14: Plan and build in a separate session (writing-plans
skill, `docs/superpowers/plans/2026-11-14-interactive-graph.md`), on its own branch. Check phase 1
live on the brain01 admin console (`ARTMIND_NO_PROXY=1`, restart `admin-ui` first: a running UI
serves stale code).

Evidence: brain01 admin trace `768efc18…` ("how many doctors have treated mummy, show me as a
graph"). The agent ran `entity-context`, `text2cypher --dryRun`, `text2cypher`, `pattern8` and
`chunks`, then wrote a hand-typed ```` ```mermaid ```` block from the rows. That diagram is good
but static, and its edges are transcribed by the model.

## 0. Problem

1. `static/admin-graph.js` adds a **View as graph** link to every user bubble. It sends the
   *question* to Explore → Graph (a second LLM call, to Cypher), then a button opens Neo4j
   Browser, which asks for a login every time. Three hops, no graph in our own UI. The link is on
   the wrong object: the question, not the answer.
2. Mermaid in chat works and stays. It is a hand-built picture, so it cannot be explored and the
   agent carries every edge in its own text.
3. Missing: an interactive graph drawn from the real graph data inside the chat page, with Cypher
   one click away.
4. `artmind-query` (482 lines, loaded every turn) must not grow. It holds no diagramming guidance
   today (grep of `SKILL.md` and `references/` finds none for mermaid or diagrams): the mermaid in
   the trace is the model's own default.

## 1. Pieces

| Piece | Holds |
|---|---|
| ```` ```artmind-graph ```` fence | JSON `{"domain": "<d>", "cypher": "<read-only Cypher>", "title": "<optional>"}`. Written by the agent; stored in the conversation as ordinary markdown, so a reloaded conversation redraws from live data. |
| `POST /api/graph/run` | Runs the fence's Cypher read-only, returns `{nodes, edges, truncated, cypher}`. In **both** UIs. |
| `static/graph-render.js` + vendored Cytoscape | Replaces each `pre > code.language-artmind-graph` with a canvas, like `mermaid-render.js` does for mermaid. Loaded on the first block only. |
| Graph drawer (admin-ui only) | A collapsible panel on the chat page: question → Cypher → Run → canvas → node inspector. Phase 2. |
| Skill `artmind-present` | When to use which picture, and how to write each. Both profiles. §6. |

Removed: the per-bubble **View as graph** link (`admin-graph.js`, its `.graph-link` CSS, its
script tag in `admin.html`). `/dashboard?tab=graph&q=` keeps working until the drawer replaces the
Explore → Graph tab (phase 2), which then keeps one secondary action, "open in Neo4j Browser".
Logging in to Browser is not fixable from here without putting credentials in a URL; not done.

## 2. `POST /api/graph/run`

New module `artmind/webui/graph_routes.py`, `register_graph_routes(app, *, mode)` called from
`create_app` for both profiles (`mode="admin"` or `"qa"`). Request `{domain, cypher}`; the domain
must be a known domain (`graph_query.normalize_domains`).

Execution (shared by both modes):

- `graph_query.read_session()` (`READ_ACCESS`: Neo4j itself refuses writes) with the parameter
  `$domains` bound to the normalised domain list. No other parameters; a `$name` other than
  `$domains` is a 400.
- `validate_read_only` (friendly pre-check), `_prepare_graph_cypher` (refuses `CALL`, appends
  `LIMIT 100` when absent; the cap below still applies to an explicit larger `LIMIT`).
- Statement timeout 15 s (the views runner uses 60 s; a drawing should be quicker) and a row cap
  of 2000 records read from the cursor.
- Results go through the existing `serialize_value` (nodes `{id, labels, properties}`,
  relationships `{id, type, start_node_id, end_node_id, properties}`, paths split into both,
  embeddings and `_prop_sources` already stripped). The endpoint walks every returned value and
  collects distinct nodes and relationships; scalar columns are ignored. A relationship whose
  endpoint is not in the result gets that node fetched by id, up to the node cap.
- Node cap 500; beyond it `truncated: true` and the response says how many were left out. The
  drawing never silently loses data.
- Per-node payload trimmed for the wire: `id`, `labels`, `name`, `_id`, `class` (the entity class
  label other than `Entity`), `observation_count`, `_valid_from`, plus a `properties` object capped
  at 40 keys and 500 characters per value. Per-edge: `id`, `source`, `target`, `type`, `rel_type`
  (the real-world meaning, from `r.rel_type`), plus capped properties.

Modes differ only in who may call it:

- **admin**: any Cypher passing the checks above.
- **qa (chat-ui)**: the same checks, plus `$domains` must appear in the Cypher (the existing
  `validate_domain_scoped`), and a per-IP rate limit (10 runs / minute). chat-ui already lets its
  agent run `text2cypher` over every domain the vault has, so a user can read nothing new; the
  added exposure is a user-chosen expensive query, which the timeout, row cap and rate limit
  bound. **Not done in v1**: signing fences server-side so only agent-written Cypher is accepted
  (needs signing at stream time and re-signing on conversation load; see §8 Q1).

Nothing is traced as a query span: this is presentation, not retrieval. The run is logged at INFO
with domain and statement length, not the Cypher text, because chat-ui Cypher can carry user text.

## 3. Renderer (`graph-render.js`)

Same pattern as `mermaid-render.js`: `window.renderArtmindGraph(scope)`, called wherever
`renderMermaid(scope)` is called today (`app.js` lines 186 and 401, the Views tab); idempotent; a
block that fails keeps its fence as a code block plus a one-line note. The fence is parsed with
`JSON.parse`; an unparseable fence is shown as code, not run.

- **Library**: Cytoscape.js, MIT, vendored at `static/vendor/cytoscape.min.js` (and `fcose` with
  its two small dependencies for the default force layout), loaded on the first block only, pinned
  to an exact version in the commit message. Canvas rendering; fine to a few thousand elements,
  and the node cap is 500.
- **Look**: node colour by entity class (a stable hash to a palette that passes contrast in light
  and dark, re-applied when `data-theme` changes, as mermaid does); label is `name`; size from
  `observation_count` (clamped); edge label is `rel_type`, falling back to the relationship
  type; structural nodes (`Document`, `DocChunk`) a distinct shape.
- **Interaction**: wheel zoom, drag pan, drag nodes, click a node to select it and show an
  inspector (properties, class, `_valid_from`); double-click a node expands its neighbours by
  running a fixed one-hop Cypher on the node's `_id` through the same endpoint (merged into the
  canvas, capped at the node cap). Hover highlights neighbours. A toolbar: **expand** (the
  full-window viewer from `mermaid-render.js`, factored to a shared module rather than copied),
  **layout** (force, concentric, breadthfirst), **copy Cypher**, **PNG**. In admin-ui, **edit
  Cypher** opens the drawer with this block's Cypher.
- All graph text is set with `textContent` or Cytoscape's text labels; nothing from the graph is
  ever put in `innerHTML` (conversations hold private corpus content).
- Failure copy distinguishes: empty result ("the query returned no nodes", showing the Cypher),
  truncated (the cap and how many were left), timeout and Neo4j errors (one line, not a stack).

## 4. Graph drawer (admin-ui, phase 2)

A panel toggled from the chat header; stays open while the conversation continues. It replaces
the Explore → Graph tab's hand-off:

1. Domain picker (most populated first, remembered; reuses `/api/explore/domains`).
2. Question box → **Generate** fills the editor via the existing `/api/explore/cypher`
   (`generate_cypher(graph_mode=True)`).
3. Editable Cypher; **Run** posts to `/api/graph/run`; the canvas is the §3 component.
4. Inspector on node click, with **Ask about this** (puts "tell me about <name>" in the chat input;
   it never sends) and **Neighbours**.
5. Secondary: **Open in Neo4j Browser**, from `/api/explore/cypher/link` as today.

`/api/explore/cypher*` stay; the Explore → Graph tab is kept as a thin page hosting the same
component so existing links keep working.

## 5. Conversations, traces, replay

- The fence is in the assistant's markdown, so `conversations/store` and `replay.build_context`
  need no change; a replayed answer's block redraws on open. Replay text sent to the model keeps
  the fence (small).
- The endpoint's data is not stored. A graph that changed since the answer redraws differently;
  that is intended (the title says what was asked; the answer text carries the claim).
- Traces: the agent's own retrieval calls are traced as today. The `artmind-present` skill load
  shows as `Skill:artmind-present`. No new trace tables.

## 6. The skill `artmind-present`

New `artmind/skills/artmind-present/SKILL.md` (about 60 lines; a `references/` file only if it
grows past ~100), added to `QA_PROFILE` and `ADMIN_PROFILE` in `webui/profiles.py`, not to the benchmark profile
(benchmarks grade text; a picture only costs tokens).

Contents:

- **When**: the user asks to show, draw, plot, map or graph something. Run retrieval first
  (`artmind-query`), then present.
- **Choosing**: a small table for lists and comparisons of attributes; **mermaid** for a hand-built
  picture of 5-15 nodes whose facts are already in hand (a short timeline, a process, one
  relationship); **artmind-graph** for "how are these connected" or "show the network" over graph
  data, more than ~15 nodes, or when the user wants to explore.
- **Mermaid rules** (the failures the renderer falls back on): quote every label, `<br/>` for a
  second line, no parentheses or colons unquoted, at most 15 nodes, `graph LR` for chains and
  `timeline` for dated sequences.
- **artmind-graph rules**: obtain the Cypher from `artmind query graph text2cypher`'s graph-mode
  output or write a pattern directly; return whole nodes and relationships (`RETURN a, r, b`),
  bind every relationship, scope with the `$domains` predicate, bound entities not paths, end
  with `LIMIT`; one fence per answer unless asked; the fence JSON on one line per key.
- **Honesty**: the picture shows what retrieval found. Keep the answer's caveat (undercount,
  ambiguity) in the text beside it; never add an edge the results do not hold.
- Not for numbers: charts of figures are out of scope (no chart library).

`artmind-query` gets **one line** in its routing area (`SKILL.md`): "Asked to show, draw or graph
the answer: after retrieval, load `artmind-present`." It adds no reference file. Source of truth is
`artmind/skills/`; a new skill reaches vaults with `artmind init` (inside the vault) and
`~/.artmind` with `artmind setup` from `~`, per CLAUDE.md §2. `just dev-refresh-skills` links it
into the checkout.

## 7. Phases

1. **Block + endpoint + skill.** Remove the bubble link. `graph_routes.py` (§2), vendored
   Cytoscape + `graph-render.js` + shared viewer (§3), the skill and the one routing line (§6),
   styles. Both UIs. Exit: on brain01 admin console, "how many doctors treated mummy, show me as a
   graph" produces a drawn, draggable graph whose Cypher matches the trace; chat-ui draws the same
   block; reload restores it; a write-looking and a `CALL` Cypher are refused.
2. **Drawer.** §4, the Explore → Graph tab re-hosted on it, neighbour expansion in blocks.
3. **Polish.** Time slider on `_valid_from` (suits the journal domains), saved graph as a view
   (`artmind-create-view` hand-off), export.

## 8. Open questions

1. **chat-ui fence provenance. Decided 2026-11-14: no graphs in chat-ui.** chat-ui keeps mermaid and tables; `/api/graph/run` is registered on the admin console only, the qa mode was removed, and `artmind-present` tells the chat-ui agent not to write artmind-graph blocks. Reopening it needs fence signing or a server-side domain filter on results (the `$domains` presence check alone is passed by `... OR true`). The question as first written: v1 trusts any read-only, `$domains`-scoped, capped, rate-limited
   Cypher from a chat-ui browser. Signing agent-written fences (HMAC at the point the final text
   is recorded, re-signed when a stored conversation is served) would restrict it to
   agent-written Cypher. Worth it only if chat-ui is reachable by people who should not run
   Cypher; chat-ui's access control beyond the Host-header allow-list was not checked in
   writing this spec. Decide before phase 1 ships chat-ui support, or ship phase 1 admin-only.
2. **Library size and licence** for Cytoscape and `fcose` are from memory: confirm exact version,
   minified size and licences when vendoring, and add them to `docs/` where the other vendored
   libraries are recorded (if recorded).
3. **Neo4j NVL** was not evaluated beyond name; revisit only if Cytoscape's look is rejected.

## 9. Not in this spec

Write-back from the graph (editing entities), 3D or WebGL rendering, numeric charts, replacing
mermaid, changes to `text2cypher` itself, and any change to retrieval or traces.
