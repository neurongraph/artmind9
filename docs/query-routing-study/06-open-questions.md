# 06 — Open questions

Settle these with evidence before writing specs. Mark each with the round that answered it.

1. **Backend vs skill.** How much of P1/P2/P4 is the `acp` backend's model rather than the
   skill's length? Needs the same questions on the Claude SDK backend (or the benchmark on
   both).
2. **Structured store routing (S7–S9).** How do agents actually classify questions in
   `my_work`? Do they read `structured-store.md`? Do unconfirmed bridge mappings mislead
   them?
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
