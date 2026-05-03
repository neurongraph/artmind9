# artmind9 task runner

# list available recipes
default:
    @just --list

# ── test ──────────────────────────────────────────────────────────────────────

# run all tests
test:
    uv run --group dev pytest test/ -v

# ── artmind domains ───────────────────────────────────────────────────────────

# list all available domain schemas
domains-list:
    uv run artmind domains list

# add a domain schema from a YAML file  (usage: just domains-add path/to/schema.yaml)
domains-add file:
    uv run artmind domains add {{ file }}

# list entities in a domain  (usage: just domains-entities <domain>)
domains-entities domain:
    uv run artmind domains entities {{ domain }}

# list relationships in a domain  (usage: just domains-relationships <domain>)
domains-relationships domain:
    uv run artmind domains relationships {{ domain }}

# delete a domain schema  (usage: just domains-delete <domain>)
domains-delete domain:
    uv run artmind domains delete {{ domain }}

# ── artmind ingest ────────────────────────────────────────────────────────────

# ingest a file or directory synchronously  (usage: just ingest-sync path/to/file [domain])
ingest-sync file domain="general":
    uv run artmind ingest sync {{ file }} --domain {{ domain }}
