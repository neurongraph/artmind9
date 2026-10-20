# 04 — Scenario catalogue

One row per question *shape* (anonymised). The ideal route is what the current CLI
supports best; "fixed by" points at elements of `03-proposal.md`. Add a scenario when a
new shape appears; when an existing one recurs, update its evidence column with the round.

Coverage status: ✅ evidence seen · ◐ seen once / partial · ☐ not yet seen (a gap to probe).

| Id | Shape | Store | Ideal route | Observed | Fixed by | Evidence | Status |
|---|---|---|---|---|---|---|---|
| S1 | Count/list events by PERSON to LOCATION in YEAR, a saved view covers it | graph | `views run trips_by_year --param …=@id` (≤ 4 calls with resolve) | 2 of 5 used the view; 3 skipped it | C2, A | R1 P1 | ✅ |
| S2 | Follow-up turn in the same conversation, new question a view covers | graph | match the new question against the cached catalogue → `views run` | skill not reloaded; remembered command run directly | C2, A (short skill), plan per turn | R1 P1, P4 | ✅ |
| S3 | Count/aggregate over events, no view fits | graph | resolve → `text2cypher --dry-run` → run; or `timeline` filtered | 14–28 calls, text2cypher retries, vector-text, note reads | C4, D (`next`), view proposal | R1 P2 | ✅ |
| S4 | A view runs and returns empty but user expects rows | graph | report empty + resolved params; one targeted check (vector-text over the window) | fan-out across timeline/pattern10/vector-text/notes | A (`views.md`), D (`next` on views) | R1 P3 | ◐ |
| S5 | History of an entity, incl. one a correction removed; what a document's versions asserted | graph | `entity-history --name/--key`; `document-history` | text2cypher / metadata; some turns ran nothing | C4 (history intent), A | R1 P5 | ✅ |
| S6 | "Explain insight X and check it against the graph" | graph | `insights show` → `entity-context` per subject | well-routed, 5–9 calls; minor detours | — (works) | R1 P6 | ✅ |
| S7 | Analytical aggregate over a table ("average/total X by Y") | SQL | `db schema` → `text2sql` | — | C5 | — | ☐ |
| S8 | Hybrid: graph entity + number in a table | both | `resolve-key` → `text2sql` → optional `entity-context` | — | C5 | — | ☐ |
| S9 | Records-plus-guidance: rows in a state + what policy says to do | both | `db sql` selecting bridge columns → `vector-text <values> --asOf today` | — | C5 | — | ☐ |
| S10 | "Tell me about X" for a hub entity | graph | `entity-context --query` (+ `--relType`) | — | C3 | — | ☐ |
| S11 | Hierarchy: everyone under X / chain of command | graph | `hierarchy` discovery → walk | — | C4, D (`next` → pattern1 fallback) | — | ☐ |
| S12 | How are X and Y related | graph | `pattern6` → `pattern5 --mode shortest` | — | D (fused `relate`) | — | ☐ |
| S13 | Conflicting claims across documents/domains | graph | conflicts + compare grounded claims | — | A (`conflicts.md`) | — | ☐ |
| S14 | Point-in-time / "as of DATE" | either | one `asOf` threaded through every step | — | C4 | — | ☐ |
| S15 | Area-level question across sibling domains ("our policies on X"), or a corpus/vault name used as if it were a domain | graph | `domains-overview` → pick the sibling set → multi-domain resolve + vector-text across all | R2: corpus name taken as the domain, Route skipped, empty results read as "no data" | C1, D (unknown-domain guard) | R2 P8, P9 | ◐ |
| S16 | Summarise / list a document's content | graph | `pattern10 --documentName` | — | C4 | — | ☐ |
| S18 | Inventory: "what documents of kind K exist" (K = a document type) | graph | `domains-overview` (its per-domain document lists answer it), then `entity-listing --entityClass` if classes are asked for | R2: wrong domain, zero rows, "nothing ingested" | C1, D (unknown-domain guard) | R2 P8, P9 | ◐ |
| S17 | Filing / tags / project questions ("notes tagged X") | graph | `filing-listing` (not in the skill today) | — | A | — | ☐ |

## "Enough to write specs" — proposed bar

- Every ☐ row seen at least once, in at least two vaults/domains where it applies.
- S7–S9 seen in `my_work` (structured store).
- At least one round on a non-`acp` backend (Claude SDK), to separate skill design from
  backend weakness. (Round 2 is one turn on `claude-sdk` / `claude-haiku-4-5`: a start, not
  enough.)
- Every proposal element in `03-proposal.md` backed by at least one ✅ scenario, or dropped.
