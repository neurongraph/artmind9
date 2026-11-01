# Passage-level provenance in chat answers

## Status

Approved design. Implementation starts only after review of this document.

## Problem

`artmind-query` currently asks the agent to emit a document-level `### Sources`
section. In the web UIs, that citation opens the original vault document in
Obsidian. It works for short sources, but a reader of a long document cannot
quickly reach the exact passage behind a graph fact or retrieval result.

Artmind already maintains source chunks under:

```text
.artmind/data/documents/markdowns/<document-key>_chunks/chunk_NNN.md
```

and graph `DocChunk` ids deterministically identify a document and chunk
sequence. An Obsidian manual test established that an explicit `obsidian://open`
URI may open one of these hidden paths. The web UI currently rejects all
hidden-folder paths before it invokes Obsidian, so generated chunk citations
need a narrow exception rather than a visible mirror of the cache.

The agent must not calculate document keys, reconstruct cache paths, or format
citations. Those rules are mutable implementation detail and source links must
be deterministic.

## Goals

- Automatically append source provenance in both chat-ui and admin-ui.
- Keep a document citation plus indented exact chunk citations.
- Make a cited chunk open its cached Markdown file in a new Obsidian tab.
- Support both graph-derived and vector-retrieval evidence through one model.
- Preserve privacy: traces contain locations and identifiers, never chunk or
  passage text.
- Gracefully omit citations when a result has no established source lineage or
  the local cache cannot be resolved.
- Keep all source-path and Markdown-format knowledge out of the
  `artmind-query` skill and the agent's system prompt.

## Non-goals

- Deep-linking to a character range inside an open Markdown editor.
- Exposing arbitrary files under `.artmind` to Obsidian or the web UI.
- Mirroring generated chunks into a vault-visible source directory.
- Changing source provenance for terminal CLI calls, benchmarks, or saved
  answers outside chat-ui/admin-ui in this increment.
- Asking an LLM to decide the source-path structure or synthesize citations.

## User experience

At the end of an answer based on query evidence, the UI appends a system-owned
source block. The agent's answer body remains unchanged.

```markdown
### Sources
- [Credit Agreement.pdf](<agreements/Credit Agreement.pdf>) — lending
  - [Passage: Chunk 14 — Events of Default](<.artmind/data/documents/markdowns/Credit Agreement~a1b2c3d4e5f6_chunks/chunk_014.md>)
```

When vector retrieval identified a particular passage, the label includes the
matching range while the link still opens the complete chunk for context:

```markdown
  - [Passage: Chunk 14 — Events of Default (characters 320–1480)](<.artmind/data/documents/markdowns/Credit Agreement~a1b2c3d4e5f6_chunks/chunk_014.md>)
```

Table-backed facts retain a document/table link only:

```markdown
### Sources
- [loans.xlsx](<tables/loans.xlsx>) — lending (table)
```

The block is omitted when no source location can be proven and resolved.

## Architecture

```text
artmind query output
      |
      v
trace evidence summarizer (location-only, no corpus text)
      |
      v
completed turn's spans
      |
      v
artmind.provenance
  select -> collect -> resolve -> render
      |
      v
conversation persistence + final SSE UI block
```

The mechanism is independent of retrieval method. Graph and vector query
commands both produce source evidence; provenance code renders it without
inferring which one is more authoritative.

### `artmind.provenance`

Introduce a focused module with one deep responsibility: turn a selected trace
span's compact evidence into safe, vault-local source Markdown.

Public operations are deliberately separated:

1. `select_final_evidence_span(spans)` is pure. It walks newest to oldest and
   chooses the first successful, non-discovery query span that has source
   evidence which could render. This is the final productive
   **evidence-bearing** span, not simply the final successful query.
2. `collect(span)` is pure. It normalizes trace evidence into a typed internal
   provenance model, preserves retrieval order, groups chunks under documents,
   deduplicates chunks, and retains passage ranges supplied by the query.
3. `resolve(provenance, vault)` performs local filesystem/registry resolution
   and returns validated, vault-relative targets plus non-fatal diagnostics.
4. `render(resolved)` produces the source block Markdown and structured counts.

The module does not read source text, call Neo4j, call an LLM, or launch
Obsidian.

### Result type

The renderer returns a structured result rather than throwing when an optional
source cannot be located:

```python
RenderedSources(
    markdown: str | None,
    cited_documents: int,
    cited_chunks: int,
    diagnostics: list[str],
)
```

Diagnostics are for logs, tests, and operator tooling. End users see neither
internal document identifiers nor filesystem failure details.

## Trace evidence contract

`artmind.traces.evidence.summarize` continues to return its existing
`chunks`, `documents`, and `entities` fields for trace analysis/review
compatibility. It adds an optional `provenance` field sufficient for citation
rendering:

```json
{
  "provenance": {
    "documents": [
      {
        "doc_id": "019...",
        "path": "agreements/Credit Agreement.pdf",
        "source_path": "agreements/Credit Agreement.pdf",
        "domain": "lending",
        "chunks": [
          {
            "chunk_id": "019..._014",
            "sequence": 14,
            "name": "Chunk 14/102",
            "header_path": "Events of Default",
            "passages": [{"char_start": 320, "char_end": 1480}]
          }
        ]
      }
    ],
    "tables": [
      {"source_file": "tables/loans.xlsx", "domain": "lending"}
    ]
  }
}
```

Rules:

- All fields are optional: old spans and commands without lineage remain valid.
- Store no chunk body, passage body, graph property value, LLM prompt, or LLM
  response in this structure.
- Preserve the existing evidence cap and deduplication behavior.
- Preserve output encounter order. In a vector result, order is relevance
  order; in graph output, it is query output order.
- A vector result contributes its matched `char_start` / `char_end` range.
  Graph results normally contribute chunks without a passage range.
- `header_path`, name, and sequence are presentation hints. A missing hint does
  not prevent a chunk citation.

The summarizer recognizes chunks and documents across the query payload shapes
already supported by the trace system, including `vector-text`, `chunks`,
`entity-context`, graph patterns, and conflict evidence. It preserves table
source files from `text2sql` separately.

## Evidence selection and graph-only behavior

The source block is based on exactly one span: the newest productive span with
renderable provenance. This avoids an exploratory query's sources being merged
into a later answer, but prevents a trailing metadata/count query from erasing
the evidence established by the query immediately before it.

| Query/result shape | Automatic provenance |
| --- | --- |
| `entity-context` with `DocChunk` sources | Document plus exact chunks |
| Graph patterns 2/3/4 with `doc_sources` | Document plus exact chunks |
| `query chunks` | Document plus requested chunks |
| Graph conflict evidence | Document plus exact chunks |
| `vector-text` | Document plus exact chunk, with passage range label |
| Graph fact derived from a structured table | Table/source-file link only |
| Metadata, counts, structural schema | No provenance; selector continues backwards |
| Graph view exposing chunks/documents | Citations from exposed lineage |
| Graph view exposing aggregates only | No automatic citation unless the view contract exposes lineage |
| Query-only host or missing cache | Retain a resolved document/table link if possible; omit unavailable chunk links |

Consequently graph-only queries are first-class. A graph provenance path is
already `Entity -> AGGREGATES -> Observation -> EXTRACTED_FROM -> DocChunk`.
The same exact chunk renderer works whether that path was found through a graph
projection or through vector passage retrieval.

## Cache-path resolution

Graph chunk ids are `{doc_id}_{sequence:03d}`. The `documents` registry maps a
document identity to its current vault-relative path/domain; `kg_chunk_status`
records `doc_id` and sequence status but is not a canonical file-path index.

The resolver must reuse existing Artmind identity/ingest rules rather than
derive a folder from the current filename itself:

1. Use `doc_id` to find the registered document record where available.
2. Resolve the actual source document identity and domain.
3. Use existing document-key/chunk-path helpers and their compatibility rules
   to find the cache folder.
4. Check the expected zero-padded leaf:
   `chunk_{sequence:03d}.md`.
5. Resolve the candidate and verify it is an existing regular file underneath
   the canonical markdown-cache root.
6. Return the vault-relative path only after all checks succeed.

This covers vault-native notes, converted binary sources, legacy bare-stem
folders, same-name document keys, collision fallbacks, and moved documents that
keep an earlier derived key. A missing registry row or cache is a normal
non-fatal outcome, not an invitation to guess a path.

## UI and conversation lifecycle

### Trace capture

Admin UI already creates trace contexts. Chat UI must opt into the same
`trace=True` backend factory and `trace_turns=True` application behavior, so
both surfaces use the same final-turn provenance path.

### Finalization

When a turn completes successfully:

1. Finalize the trace first, including span-to-tool matching.
2. Load the completed spans and call `artmind.provenance`.
3. If `markdown` is non-empty, append/replace the source block in the final
   answer.
4. Persist the augmented answer and its Markdown block(s) in conversation
   history.
5. Emit one final SSE event so the live browser appends the same block to the
   rendered turn.

If the agent emitted a trailing `### Sources` section despite its simplified
instructions, the system replaces that section rather than rendering two source
lists. The system-owned block is authoritative.

The source block is saved with the conversation. On reload,
`conversations.js` renders the stored Markdown and applies the same safe link
handling; it does not need to recompute provenance.

### Agent and skill changes

Remove the requirement that `artmind-query` render a `### Sources` section,
and remove the equivalent chat/admin profile prompt instructions. Keep the
skill's requirements to retrieve and ground an answer in Artmind query output.
Citation selection, path resolution, and Markdown rendering are backend work.

## Obsidian opening and hidden-path security

The existing generic vault-link validator continues to reject hidden folders.
Add a distinct generated-chunk validation path, used only for links generated
by the provenance renderer. It permits only an existing regular file that:

- resolves beneath `.artmind/data/documents/markdowns/` in the active vault;
- has a `*_chunks/chunk_NNN.md` relative shape; and
- remains under that root after resolution (including symlink resolution).

The API must not allow arbitrary user-supplied `.artmind` paths merely because
they match Markdown link syntax. The server repeats validation before producing
the existing `obsidian://open` URI. The URI removes the `.md` extension and
opens a new tab, as established by the current source-link behavior and the
manual Obsidian test.

This explicitly keeps all other hidden data inaccessible through source links:
configuration, registry databases, logs, staging data, embeddings, artifacts,
and arbitrary cache files remain rejected.

## Error handling

- Trace unavailable: answer streams and saves normally; no automatic sources.
- No productive evidence-bearing span: no automatic sources.
- Registry/cache missing for a chunk: cite its resolved document when possible,
  omit the chunk; record an internal diagnostic.
- Document path unavailable but chunk cache validates: render a chunk-only
  source entry only if it can be safely attributed to a domain; otherwise omit
  it.
- Invalid/stale/malicious source target: reject it; never invoke the OS URL
  handler.
- Finalization/rendering failure: log it and preserve the original answer.
  Source rendering must never fail a completed user turn.

## Testing

### Trace evidence

- Vector result records one chunk with its passage range and document metadata,
  without chunk text.
- Graph patterns, entity-context, chunks, and conflict payloads record exact
  chunk lineage.
- Table-only provenance records a source file with no chunk.
- Legacy/minimal payloads remain compatible with existing evidence summaries.
- Caps, deduplication, and encounter ordering remain deterministic.

### Provenance module

- Picks the latest productive evidence-bearing span and skips a later
  metadata-only span.
- Does not merge exploratory earlier spans.
- Renders graph chunk, vector range, document-only, table-only, and empty
  evidence cases.
- Resolves keyed, legacy, moved, collision-safe, and converted-binary chunk
  caches through current identity helpers.
- Produces no broken link when registry/cache files are absent.

### Web UI and security

- Chat and admin create traces and append the same system-generated source
  block after a traced query answer.
- Saved/reloaded conversation renders exactly the stored source block.
- An existing generated chunk gets the expected `obsidian://open` URI.
- The API rejects every other `.artmind` path, paths outside the vault, missing
  files, traversal attempts, and symlink escapes.
- A failure while rendering provenance leaves a successful answer intact.

## Compatibility and rollout

No database migration is required: trace evidence is JSON and old rows simply
lack `provenance`. Existing trace analysis continues to consume the retained
`chunks` and `documents` fields. No generated cache files are added or changed;
the feature consumes the cache created by ingestion.

The change applies only to the two web UIs. CLI and benchmark source formatting
remain unchanged until a later decision explicitly extends the renderer to
those surfaces.
