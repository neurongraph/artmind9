"""kg_views: saved, parameterised, read-only graph queries with agent guidance.

Design: docs/superpowers/specs/2026-10-09-kg-views-design.md

model   -- the view.yaml contract (pure)
render  -- deterministic markdown per presentation format (pure)
params  -- --param strings -> typed Cypher bindings, entity auto-resolve
store   -- where views live; list / load / save / delete; the version guard
runner  -- execute a view and build the result envelope
"""
