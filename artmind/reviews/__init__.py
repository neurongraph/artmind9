"""Review dossiers: diagnosed query failures and the fixes they propose
(docs/superpowers/specs/2026-10-11-query-review-loop-design.md §7-§8).

`dossier` owns the file format, `validate` the completeness rules,
`scaffold` builds a new dossier from a trace, `index` keeps the local
`trace_reviews` index in step with the files, and `leakcheck` scans a code
checkout's diff for private evidence. Dossiers hold private corpus data.
"""
