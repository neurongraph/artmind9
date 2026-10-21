# 06 — Open questions

Settle these with evidence before writing specs. Mark each with the round that answered it.

1. **Backend vs skill.** How much of P1/P2/P4 is the `acp` backend's model rather than the
   skill's length? Needs the same questions on the Claude SDK backend (or the benchmark on
   both). *Round 2, partial:* one turn on `claude-sdk` / `claude-haiku-4-5` skipped Route
   (P8), so a weaker model fails here too. *Round 3, mostly answered:* 36 turns on `claude-sdk` /
   Haiku 4.5 show the same failures as `acp` (P12–P14), so they are not `acp`-only. Open: one
   larger-model run of the whole set. Q10 on Sonnet 5 failed on a `.md` name, so a bigger model
   alone does not remove P12.
2. **Structured store routing (S7–S9).** How do agents actually classify questions in
   `my_work`? Do they read `structured-store.md`? Do unconfirmed bridge mappings mislead
   them? *Round 3, partial (banking_corpus, confirmed mappings):* classification was right in 5
   of 5; the misses were policy-side completeness and invalid command forms (P15). `my_work`
   (unconfirmed mappings) is still unseen.
3. **Lexical intent rules.** Across rounds, how many questions would simple keyword/regex
   rules classify correctly? If most, `query plan` stays deterministic; if not, where does
   a cheap LLM classifier earn its place?
4. **View matching.** Would embedding similarity on `summary` + `examples` have matched
   every question that a view answers (S1, S2) without false matches on S3-type
   questions? Needs a labelled list of (question shape, right view or none).
5. **Param filling.** Can entity params be filled by resolving noun phrases filtered on
   the param's `entity_class` reliably, including nicknames and spelling variants?
6. **Empty view results (S4).** Is an empty view result usually a data gap (extraction),
   a view bug, or a resolution miss? The answer decides what `next` should suggest.
7. **Follow-up turns (S2).** Is the fix "`query plan` on every turn" enough, or does the
   chat surface need to re-inject a short routing reminder on follow-ups?
8. **Multi-domain / area questions (S15).** How often do users name an area rather than a
   domain, and does domain matching from `domains-overview` pick the right set?
9. **Command consolidation.** Which overlapping commands are actually chosen wrongly in
   traces (pattern2/3/4 vs entity-context; 6 vs 5; resolvers)? Consolidate only those.
10. **Is the routing sub-agent ever used?** No round-1 turn launched it. If it never
    appears, drop it in A rather than replacing it.
11. **Unknown domain: warn or fail?** (R2 P9; *warns, implemented 2026-10-20 — revisit if callers need a hard failure.*) A warning keeps scripts working and lets the
    agent self-correct; an error is louder but breaks callers that probe domains. Does the
    plugin or any skill rely on an empty result for a domain with no data yet?
12. **How often is a corpus or vault name used as a domain?** (R2 P8, S15/S18.) One turn so
    far. Count across vaults before deciding whether the guard (D) alone suffices or Route
    must become deterministic (C1). *Round 3:* 6 of 36 turns tried an invented domain first;
    the guard fired every time and the next call was `domains-overview` (P11). The guard
    suffices for correctness here; C1 would save the wasted call.
13. **Is the retired-document blind spot a CLI gap or a skill gap?** (R3 P12.) *Decided
    2026-10-21 (design, not yet specified):* both, graph-side. Retired chunks stay out of the
    vector/fulltext index (they are absent by label swap; there is no currency predicate);
    `--includeHistory` is dropped. The graph already reaches history, so add `supersedes` to
    document rows, make `--asOf` honest, hint on empty `--documentName`, and add a skill
    rule. Remaining check: confirm `vector-text --asOf` as a floor-only filter
    (`valid_from <= asOf`, not a snapshot) is documented as such or removed.
14. **`.md` suffix on `--documentName`.** Zero rows without a hint cost Q10 about 20 turns.
    Normalise (accept both) or hint? Check whether any caller relies on exact matching.
15. **Supersession direction at ingest.** Root cause found (R3, 2026-10-21): in the
    `banking_corpus` policy pair the adjudicator record names the older file as the newer
    document, so the newer file is retired. Open: how often the adjudicator inverts the
    direction (check the other three records; the February schedule -> `welcome_pack.md`
    record looks suspicious, and the February -> March schedule supersession is missing
    entirely), and whether a document-date or "Supersedes <version>" text check should
    override the verdict. Belongs to ingest, but Q21/Q25 grades depend on it.
16. **Do views-first checks belong in `traces stats` for view-less vaults?** (R3 P10.) The
17. **Is the grading rubric stable?** (R3.) Six graders, one rubric, no second pass. Spot-
    check three grades by hand before using the 9/24/3 split as a baseline for a later run;
    two benchmark notes are themselves stale.
18. **Adjudicator supersession scope.** (R3, Q11.) All four records use `scope: document`,
    which retires the whole older document. Q11's "how rates change" section exists only in
    the retired January schedule, so a document-wide retirement removes content that the
    newer schedule does not restate. Should the adjudicator default to a narrower scope when
    the newer document covers only part of the older one? Decide with the owner before any
    spec; it changes what is current, not just how it is found.
19. **Per-change test strategy.** The full benchmark runs once, as the final gate, after the
    changes pass unit tests, deterministic probes and targeted agent runs (see
    `03-proposal.md`, Testing strategy). Open: which three or four questions are the
    smallest set that still shows each change working.
