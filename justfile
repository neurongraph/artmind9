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
    uv run artmind domains add '{{ file }}'

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
    uv run artmind ingest sync '{{ file }}' --domain {{ domain }}

# dry-run entity resolution: compute merge proposals and write to file  (usage: just ingest-refine-graph-dry [domain])
ingest-refine-graph-dry domain="":
    uv run artmind ingest refine-graph --dry-run {{ if domain != "" { "--domain " + domain } else { "" } }}

# apply merge proposals from a dry-run file  (usage: just ingest-refine-graph-apply <file> [domain])
ingest-refine-graph-apply file domain="":
    uv run artmind ingest refine-graph --from-file '{{ file }}' {{ if domain != "" { "--domain " + domain } else { "" } }}

# ── artmind docs ──────────────────────────────────────────────────────────────

# clean a document from storage, registry, and Neo4j  (usage: just docs-clean <domain> <document>)
docs-clean domain document:
    uv run artmind docs clean --domain {{ domain }} {{ document }}

# ── artmind query ─────────────────────────────────────────────────────────────

# graph metadata for a domain  (usage: just query-graph-metadata <domain>)
query-graph-metadata domain:
    uv run artmind query graph metadata --domain {{ domain }}

# entity listing for a domain  (usage: just query-graph-entities <domain>)
query-graph-entities domain:
    uv run artmind query graph entity_listing --domain {{ domain }}

# list entities of a class  (usage: just query-graph-list <domain> <entity_class>)
query-graph-list domain entity_class:
    uv run artmind query graph pattern1 --domain {{ domain }} --entityClass {{ entity_class }}

# info on a named entity  (usage: just query-graph-info <domain> <entity_name>)
query-graph-info domain entity_name:
    uv run artmind query graph pattern2 --domain {{ domain }} --entityNameList "{{ entity_name }}"

# top-N entities of a class  (usage: just query-graph-top <domain> <entity_class> [topN])
query-graph-top domain entity_class top_n="5":
    uv run artmind query graph pattern9 --domain {{ domain }} --entityClass {{ entity_class }} --topN {{ top_n }}

# direct relationships between two entities  (usage: just query-graph-rel <domain> <entity1> <entity2>)
query-graph-rel domain entity1 entity2:
    uv run artmind query graph pattern6 --domain {{ domain }} --entityName1 "{{ entity1 }}" --entityName2 "{{ entity2 }}"

# search chunks by vector + text (RRF combined)  (usage: just query-text <domain> "question")
query-text domain question top_k="5":
    uv run artmind query vector_text --domain {{ domain }} --topK {{ top_k }} "{{ question }}"
