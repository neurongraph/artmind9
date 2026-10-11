"""Query traces: capture, storage and analysis of admin-console and benchmark
turns (docs/superpowers/specs/2026-10-11-query-review-loop-design.md).

`store` owns the SQL, `evidence` reads a query command's output payload,
`path` derives the retrieval path, `span` is the CLI side of capture,
`recorder` the turn side, and `analysis` the read surface behind
`artmind traces`.
"""
