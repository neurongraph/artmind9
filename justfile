# artmind9 task runner

# list available recipes
default:
    @just --list

# ── test ──────────────────────────────────────────────────────────────────────

# run all tests
test:
    uv run --group dev pytest test/ -v

# ── artmind setup ─────────────────────────────────────────────────────────────

# initialize SQLite tables and Neo4j constraints/indexes (idempotent)
setup:
    uv run artmind setup

# ── artmind domains ───────────────────────────────────────────────────────────

# list all available domain schemas
domains-list:
    uv run artmind domains list

# add a domain schema from a YAML file  (usage: just domains-add path/to/schema.yaml)
domains-add file:
    uv run artmind domains add '{{ file }}'

# delete a domain schema  (usage: just domains-delete <domain>)
domains-delete domain:
    uv run artmind domains delete {{ domain }}

# show entities extraction prompt for a domain  (usage: just domains-entities-prompt <domain>)
domains-entities-prompt domain:
    uv run artmind domains entities-prompt {{ domain }}

# show properties extraction prompt for a domain  (usage: just domains-properties-prompt <domain>)
domains-properties-prompt domain:
    uv run artmind domains properties-prompt {{ domain }}

# show relationships extraction prompt for a domain  (usage: just domains-relationships-prompt <domain>)
domains-relationships-prompt domain:
    uv run artmind domains relationships-prompt {{ domain }}

# sync child domain schemas against their parent  (usage: just domains-harmonize [domain] [--dry-run])
domains-harmonize domain="" dry_run="":
    uv run artmind domains harmonize {{ if domain != "" { "--domain " + domain } else { "" } }} {{ if dry_run == "true" { "--dry-run" } else { "" } }}

# ── artmind ingest ────────────────────────────────────────────────────────────

# ingest a file or directory synchronously  (usage: just ingest-sync path/to/file [domain])
ingest-sync file domain="general":
    uv run artmind ingest sync '{{ file }}' --domain {{ domain }}

# submit a file or directory for background ingestion  (usage: just ingest-async path/to/file [domain])
ingest-async file domain="general":
    uv run artmind ingest async '{{ file }}' --domain {{ domain }}

# list recent ingestion jobs  (usage: just ingest-jobs [status])
ingest-jobs status="":
    uv run artmind ingest jobs {{ if status != "" { "--status " + status } else { "" } }}

# show status for a job  (usage: just ingest-job-status <job_id>)
ingest-job-status job_id:
    uv run artmind ingest job-status {{ job_id }}

# show detailed per-file results for a job  (usage: just ingest-job-results <job_id>)
ingest-job-results job_id:
    uv run artmind ingest job-results {{ job_id }}

# show live realtime status dashboard of async jobs
ingest-dashboard:
    uv run artmind ingest dashboard

# re-run KG extraction for a document  (usage: just ingest-extract-kg <document> --domain <domain>)
ingest-extract-kg document domain:
    uv run artmind ingest extract-kg {{ document }} --domain {{ domain }}

# write already-extracted KG JSON to Neo4j  (usage: just ingest-write-to-graph <document> --domain <domain>)
ingest-write-to-graph document domain:
    uv run artmind ingest write-to-graph {{ document }} --domain {{ domain }}

# batch write all document KG JSON in a folder to Neo4j  (usage: just ingest-write-to-graph-folder <folder> [domain])
ingest-write-to-graph-folder folder domain="":
    uv run artmind ingest write-to-graph --folder '{{ folder }}' {{ if domain != "" { "--domain " + domain } else { "" } }}

# pull KG JSON from an external GitHub repo  (usage: just ingest-pull-kg <repo_url> <repo_path> <domain>)
ingest-pull-kg repo repo_path domain:
    uv run artmind ingest pull-kg --repo '{{ repo }}' --repo-path '{{ repo_path }}' --domain {{ domain }}

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
    uv run artmind query graph entity-listing --domain {{ domain }}

# list entities of a class  (usage: just query-graph-list <domain> <entity_class>)
query-graph-list domain entity_class:
    uv run artmind query graph pattern1 --domain {{ domain }} --entityClass {{ entity_class }}

# info on a named entity  (usage: just query-graph-info <domain> <entity_name>)
query-graph-info domain entity_name:
    uv run artmind query graph pattern2 --domain {{ domain }} --entityNameList "{{ entity_name }}"

# entity + lightweight relationship summary  (usage: just query-graph-summary <domain> <entity_name>)
query-graph-summary domain entity_name:
    uv run artmind query graph pattern3 --domain {{ domain }} --entityNameList "{{ entity_name }}"

# entity + full neighborhood  (usage: just query-graph-neighborhood <domain> <entity_class> <entity_name>)
query-graph-neighborhood domain entity_class entity_name:
    uv run artmind query graph pattern4 --domain {{ domain }} --entityClass {{ entity_class }} --entityName "{{ entity_name }}"

# paths between two entities  (usage: just query-graph-paths <domain> <class1> <class2> <name1> <name2> [mode])
query-graph-paths domain class1 class2 name1 name2 mode="shortest":
    uv run artmind query graph pattern5 --domain {{ domain }} --entityClass1 {{ class1 }} --entityClass2 {{ class2 }} --entityName1 "{{ name1 }}" --entityName2 "{{ name2 }}" --mode {{ mode }}

# direct relationships between two entities  (usage: just query-graph-rel <domain> <entity1> <entity2>)
query-graph-rel domain entity1 entity2:
    uv run artmind query graph pattern6 --domain {{ domain }} --entityName1 "{{ entity1 }}" --entityName2 "{{ entity2 }}"

# search entities by name or description fragment  (usage: just query-graph-search <domain> <search_term> [limit])
query-graph-search domain search_term limit="10":
    uv run artmind query graph pattern7 --domain {{ domain }} --searchTerm "{{ search_term }}" --limit {{ limit }}

# entities of class X connected to entity Y  (usage: just query-graph-connected <domain> <entity_class> <entity_name>)
query-graph-connected domain entity_class entity_name:
    uv run artmind query graph pattern8 --domain {{ domain }} --entityClass {{ entity_class }} --entityName "{{ entity_name }}"

# top-N entities of a class  (usage: just query-graph-top <domain> <entity_class> [topN])
query-graph-top domain entity_class top_n="5":
    uv run artmind query graph pattern9 --domain {{ domain }} --entityClass {{ entity_class }} --topN {{ top_n }}

# LLM-generated Cypher from natural language  (usage: just query-graph-text2cypher <domain> "question" [--dry-run])
query-graph-text2cypher domain question dry_run="":
    uv run artmind query graph text2cypher --domain {{ domain }} {{ if dry_run == "true" { "--dry-run" } else { "" } }} "{{ question }}"

# search chunks by vector + text (RRF combined)  (usage: just query-text <domain> "question")
query-text domain question top_k="5":
    uv run artmind query vector-text --domain {{ domain }} --topK {{ top_k }} "{{ question }}"

# ── artmind update ────────────────────────────────────────────────────────────

# extract facts and find graph candidates  (usage: just update-draft <domain> "text" [session])
update-draft domain text session="":
    uv run artmind update draft --domain {{ domain }} --text "{{ text }}" {{ if session != "" { "--session " + session } else { "" } }}

# write confirmed facts to Neo4j  (usage: just update-confirm <session> '<resolutions_json>')
update-confirm session resolutions:
    uv run artmind update confirm --session {{ session }} --resolutions '{{ resolutions }}'

# list recent update sessions  (usage: just update-history [domain] [user] [limit])
update-history domain="" user="" limit="20":
    uv run artmind update history {{ if domain != "" { "--domain " + domain } else { "" } }} {{ if user != "" { "--user " + user } else { "" } }} --limit {{ limit }}

# export UserChat nodes to markdown files  (usage: just update-export [domain] [format] [output])
update-export domain="" fmt="sequential" output="data/chats":
    uv run artmind update export {{ if domain != "" { "--domain " + domain } else { "" } }} --format {{ fmt }} --output {{ output }}

# ── artmind session ───────────────────────────────────────────────────────────

# export Neo4j graph to a snapshot (end of session)
session-close:
    uv run artmind session close

# wipe Neo4j and restore from latest snapshot (start of session)
session-initiate:
    uv run artmind session initiate --yes
