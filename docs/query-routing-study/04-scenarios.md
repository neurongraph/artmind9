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
| S7 | Analytical aggregate over a table ("average/total X by Y") | SQL | `db schema` → `text2sql` | R3 Q32–Q36: `db bridge` read, SQL and policy kept apart; invalid command forms tried, `text2sql` ×6 once, policy side often skipped | C5, D (`next`, aliases) | R3 P15 | ◐ |
| S8 | Hybrid: graph entity + number in a table | both | `resolve-key` → `text2sql` → optional `entity-context` | R3: per-customer join worked (Q34 correct); Q35 never mapped the per-customer driver to the guidance | C5 | R3 P15 | ◐ |
| S9 | Records-plus-guidance: rows in a state + what policy says to do | both | `db sql` selecting bridge columns → `vector-text <values> --asOf today` | R3: SQL and policy kept separate in 5 of 5; Q32/Q33 missed the complaints policy, Q36 ignored n=1 | C5 | R3 P15 | ◐ |
| S10 | "Tell me about X" for a hub entity | graph | `entity-context --query` (+ `--relType`) | — | C3 | — | ☐ |
| S11 | Hierarchy: everyone under X / chain of command | graph | `hierarchy` discovery → walk | — | C4, D (`next` → pattern1 fallback) | — | ☐ |
| S12 | How are X and Y related | graph | `pattern6` → `pattern5 --mode shortest` | — | D (fused `relate`) | — | ☐ |
| S13 | Conflicting claims across documents/domains | graph | conflicts + compare grounded claims | R3: conflicts route used 0 times in 6 C-questions; 1 wrong, 3 partial, 2 correct | A (`conflicts.md`), D (`next`), time route | R3 P12, P13 | ✅ |
| S14 | Point-in-time / "as of DATE" | either | one `asOf` threaded through every step | R3: no `--asOf` in 36 turns; retired chunks are not in the vector/fulltext index; reachable only via the graph; Q07 wrong, Q09/Q11 partial (Q25 is a data error, not a time-route miss) | C4, D (`supersedes` pointer, honest `--asOf`, `next`) | R3 P12 | ✅ |
| S15 | Area-level question across sibling domains ("our policies on X"), or a corpus/vault name used as if it were a domain | graph | `domains-overview` → pick the sibling set → multi-domain resolve + vector-text across all | R2: corpus name taken as the domain, Route skipped, empty results read as "no data" | C1, D (unknown-domain guard) | R2 P8, P9 | ◐ |
| S16 | Summarise / list a document's content | graph | `pattern10 --documentName` | — | C4 | — | ☐ |
| S18 | Inventory: "what documents of kind K exist" (K = a document type) | graph | `domains-overview` (its per-domain document lists answer it), then `entity-listing --entityClass` if classes are asked for | R2: wrong domain, zero rows, "nothing ingested" | C1, D (unknown-domain guard) | R2 P8, P9 | ◐ |
| S19 | Multi-document procedure or "how do I" answer (steps, checks, escalation drawn from several SOPs, policies, guides) | graph | `vector-text` over the parent domain → `pattern10`/`chunks` per named document, follow `[[wikilinks]]` | R3: sibling domains narrowed by hand, early stop, 49% of expected documents cited (Q01–Q06, Q16–Q24, Q31) | C1, D (fan-out default, wikilink hint, ready `sources`) | R3 P14, P16 | ✅ |
| S20 | "Which version applied on date D" / "what did the earlier version say" | graph | `document-history --documentName <file>.md` + `pattern10 --asOf D`; report disagreement inside the older version | R3: answered from the current version (Q07), `.md` dropped three times (Q10) | C4, D (`next`: zero rows → `.md`; `supersedes` pointer) | R3 P12 | ✅ |
| S21 | Vault or domain with no saved views | any | `domains-overview` reports view counts; skip `views list` where zero | R3: 50 `views list` calls, all empty | C2 (conditional), D | R3 P10 | ✅ |
| S17 | Filing / tags / project questions ("notes tagged X") | graph | `filing-listing` (not in the skill today) | — | A | — | ☐ |

## "Enough to write specs" — proposed bar

- Every ☐ row seen at least once, in at least two vaults/domains where it applies.
- S7–S9 seen in `my_work` (structured store). *Round 3 saw S7–S9 in `banking_corpus` (5 tables, mappings confirmed); `my_work` still outstanding.*
- At least one round on a non-`acp` backend (Claude SDK), to separate skill design from
  backend weakness. (Round 3 is 36 turns on `claude-sdk` / `claude-haiku-4-5`, one on `claude-sonnet-5`: the
  routing failures recur on the SDK backend, so they are not `acp`-only. A larger model on
  the whole set is still untested.)
- Every proposal element in `03-proposal.md` backed by at least one ✅ scenario, or dropped.
