# artmind9 task runner
#
# Naming convention: recipes are prefixed by group so `just --list`'s
# alphabetical sort clusters them. Prefixes mirror `artmind`'s own CLI
# groups (domains, ingest, docs, query[-graph], update, session, serve),
# plus two repo-only groups that don't wrap a CLI subcommand group:
#   cli-  — top-level `artmind` lifecycle commands (init, setup)
#   dev-  — checkout/tooling operations, not `artmind` subcommands at all
#           (install, uninstall, daemon management, tests, skill sync,
#           docs generation scripts)

# The three ports artmind daemons bind. Single source of truth: `_free-port`
# uses them to stop a daemon, the serve-* recipes to (re)start one. Only
# SERVE_PORT is configurable, matching `artmind serve`'s own default.
# NB: this reads a real environment variable — setting ARTMIND_SERVE_PORT in
# ~/.artmind/.env changes the daemon but not these recipes (they don't parse .env).
SERVE_PORT := env_var_or_default('ARTMIND_SERVE_PORT', '8377')
CHAT_UI_PORT := '8378'
ADMIN_UI_PORT := '8379'

# list available recipes
default:
    @just --list

# ── cli (artmind lifecycle commands) ────────────────────────────────────────

# scaffold the run folder (~/.artmind) + data dirs; seed .env, skills, schemas
cli-init:
    uv run artmind init

# initialize SQLite tables and Neo4j constraints/indexes (idempotent)
cli-setup:
    uv run artmind setup

# ── dev (checkout/tooling, not artmind subcommands) ─────────────────────────

# install: put `artmind` on PATH and scaffold the run folder (~/.artmind).
# Editable, so code edits are live; paths are decoupled from this checkout, so
# `artmind` runs from anywhere. Then edit ~/.artmind/.env and run `artmind setup`.
# See docs/INSTALL.md. (For a checkout-independent deploy, drop `--editable`.)
dev-install: dev-stop-daemons
    uv tool install --force --editable '.[ingest]'
    artmind init

# uninstall the global artmind command (leaves ~/.artmind and data intact)
dev-uninstall:
    uv tool uninstall artmind9

# stop running artmind daemons (`serve`, `chat-ui`, `admin-ui`, ingestion worker).
# They load code at start, so one left running keeps serving the OLD build after
# a reinstall — and each holding its port makes the next `artmind <cmd>` fail to bind.
dev-stop-daemons: (_free-port SERVE_PORT "artmind serve" "0") (_free-port CHAT_UI_PORT "artmind chat-ui" "0") (_free-port ADMIN_UI_PORT "artmind admin-ui" "0")
    #!/usr/bin/env bash
    set -uo pipefail
    # The three listeners are handled by _free-port above. The ingestion worker
    # binds no port, so it's the one daemon that must be matched by script path
    # instead — and we must never match ourselves.
    for pid in $(pgrep -f "artmind/worker\.py" 2>/dev/null || true); do
        if [ "$pid" = "$$" ] || [ "$pid" = "${PPID:-0}" ]; then
            continue
        fi
        echo "stopping artmind ingestion worker (pid $pid)"
        kill "$pid" 2>/dev/null || true
        for _ in 1 2 3 4 5 6 7 8; do
            kill -0 "$pid" 2>/dev/null || break
            sleep 0.25
        done
        kill -0 "$pid" 2>/dev/null && { echo "  forcing pid $pid"; kill -9 "$pid" 2>/dev/null || true; }
    done
    # Always confirm we ran: _free-port is silent when a port was already clear,
    # so without this a no-op looks indistinguishable from the recipe not firing.
    echo "artmind daemons stopped"
    exit 0

# run all tests
dev-test:
    uv run --group dev pytest test/ -v

# dump the real command hierarchy (trust this over any prose docs)
dev-cli-help:
    uv run python scripts/click_cli_hierarchy.py artmind.cli:cli

# check every command is routed into the admin-ui CLI guide (see cli_guide.py)
dev-cli-guide-check:
    uv run --group dev pytest test/test_cli_guide.py -v

# copy (not symlink) artmind/skills into .claude/skills and .pi/skills
dev-copy-skills:
    cp -r ./artmind/skills/* ./.claude/skills
    cp -r ./artmind/skills/* ./.pi/skills

# sync artmind/skills into .claude/skills and .pi/skills as symlinks, adding new ones and removing stale/broken links
dev-refresh-skills:
    #!/usr/bin/env bash
    set -euo pipefail
    for target_dir in .claude/skills .pi/skills; do
        mkdir -p "$target_dir"
        # remove any broken symlink (e.g. links to the pre-move ./skills location)
        for link in "$target_dir"/*; do
            [ -L "$link" ] || continue
            if [ ! -e "$link" ]; then
                echo "removing stale link: $link -> $(readlink "$link")"
                rm "$link"
            fi
        done
        # add symlinks for any skill not yet linked
        for skill in artmind/skills/*/; do
            name=$(basename "$skill")
            link="$target_dir/$name"
            if [ ! -e "$link" ]; then
                echo "linking $name into $target_dir"
                ln -s "../../artmind/skills/$name" "$link"
            fi
        done
    done

# ── artmind docs ─────────────────────────────────────────────────────────────

# clean a document from storage, registry, and Neo4j  (usage: just docs-clean <domain> <document>)
docs-clean domain document:
    uv run artmind docs clean --domain {{ domain }} {{ document }}

# ── artmind domains ──────────────────────────────────────────────────────────

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

# ── artmind ingest ───────────────────────────────────────────────────────────

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

# re-queue failed files in a job for reprocessing  (usage: just ingest-retry-job <job_id> [--include-skipped])
ingest-retry-job job_id flags="":
    uv run artmind ingest retry-job {{ job_id }} {{ flags }}

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

# backfill vector embeddings for entities missing one  (usage: just ingest-embed-entities <domain>)
ingest-embed-entities domain:
    uv run artmind ingest embed-entities --domain {{ domain }}

# propose (or apply, with --from-file) the full refinement pipeline: time → supersession → merge → conflicts → consolidate → embed  (usage: just ingest-refine-pipeline <domain> [flags])
ingest-refine-pipeline domain flags="":
    uv run artmind ingest refine-pipeline --domain {{ domain }} {{ flags }}

# rewrite accumulated entity descriptions into clean prose from source chunks  (usage: just ingest-consolidate-descriptions <domain> [flags])
ingest-consolidate-descriptions domain flags="":
    uv run artmind ingest consolidate-descriptions --domain {{ domain }} {{ flags }}

# backfill canonical valid_from/valid_to/event_at from schema temporal mappings  (usage: just ingest-normalize-time <domain> [--dry-run])
ingest-normalize-time domain dry_run="":
    uv run artmind ingest normalize-time --domain {{ domain }} {{ if dry_run == "true" { "--dry-run" } else { "" } }}

# detect non-destructive conflicts between entities, intra- or cross-domain  (usage: just ingest-detect-conflicts <domain> [flags])
ingest-detect-conflicts domain flags="":
    uv run artmind ingest detect-conflicts --domain {{ domain }} {{ flags }}

# manually assert that one document supersedes another  (usage: just ingest-supersede <domain> <newer> <older> [flags])
ingest-supersede domain newer older flags="":
    uv run artmind ingest supersede --domain {{ domain }} --newer "{{ newer }}" --older "{{ older }}" {{ flags }}

# scan documents for explicit Supersession Notice sections and apply SUPERSEDES edges  (usage: just ingest-detect-supersession <domain> [--dry-run])
ingest-detect-supersession domain dry_run="":
    uv run artmind ingest detect-supersession --domain {{ domain }} {{ if dry_run == "true" { "--dry-run" } else { "" } }}

# ── artmind db (structured store) ───────────────────────────────────────────

# read the structured-to-graph bridge: class scope, bridge columns, grain  (usage: just db-bridge [domain] [entityClass])
db-bridge domain="" entity_class="":
    uv run artmind db bridge {{ if domain != "" { "--domain " + domain } else { "" } }} {{ if entity_class != "" { "--entityClass " + entity_class } else { "" } }}

# confirm a proposed bridge column  (usage: just db-bridge-confirm <table> <column> [domain])
db-bridge-confirm table column domain="":
    uv run artmind db bridge confirm --table {{ table }} --column {{ column }} {{ if domain != "" { "--domain " + domain } else { "" } }}

# remove a bridge column's role  (usage: just db-bridge-clear <table> <column> [domain])
db-bridge-clear table column domain="":
    uv run artmind db bridge clear --table {{ table }} --column {{ column }} {{ if domain != "" { "--domain " + domain } else { "" } }}

# list table classifications still awaiting confirm/reject  (usage: just db-review [domain])
db-review domain="":
    uv run artmind db review {{ if domain != "" { "--domain " + domain } else { "" } }}

# list registered structured tables  (usage: just db-list [domain])
db-list domain="":
    uv run artmind db list {{ if domain != "" { "--domain " + domain } else { "" } }}

# show or confirm what a table's rows denote: instance | lookup | normative  (usage: just db-grain <table> [grain])
db-grain table grain="":
    uv run artmind db grain {{ table }} {{ if grain != "" { "--set " + grain } else { "" } }}

# re-run structured classification (grain, bridge columns, mappings) for a table  (usage: just db-propose <table> ["--step mapping --redo"])
db-propose table flags="":
    uv run artmind db propose {{ table }} {{ flags }}

# show columns/types (+profiles/mappings) for a table  (usage: just db-schema [table])
db-schema table="":
    uv run artmind db schema {{ table }}

# run raw read-only SQL against the structured store  (usage: just db-sql "SELECT ...")
db-sql sql:
    uv run artmind db sql "{{ sql }}"

# list proposed vs confirmed column-to-entityClass mappings for a table  (usage: just db-mappings <table> [--acceptProposed])
db-mappings table flags="":
    uv run artmind db mappings {{ table }} {{ flags }}

# upsert a confirmed column-to-entityClass mapping  (usage: just db-mappings-set <table> <column> <entityClass> [confidence])
db-mappings-set table column entity_class confidence="1.0":
    uv run artmind db mappings {{ table }} set --column {{ column }} --entityClass {{ entity_class }} --confidence {{ confidence }}

# confirm an existing proposed mapping  (usage: just db-mappings-confirm <table> <column> <entityClass>)
db-mappings-confirm table column entity_class:
    uv run artmind db mappings {{ table }} confirm --column {{ column }} --entityClass {{ entity_class }}

# remove mapping(s) for a table  (usage: just db-mappings-clear <table> [column])
db-mappings-clear table column="":
    uv run artmind db mappings {{ table }} clear {{ if column != "" { "--column " + column } else { "" } }}

# rebuild the Neo4j catalogue subgraph for a domain on demand  (usage: just db-catalogue <domain>)
db-catalogue domain:
    uv run artmind db catalogue --domain {{ domain }}

# re-ingest a table from its recorded source file (replace or SCD-2 temporal merge)  (usage: just db-refresh <table> [domain])
db-refresh table domain="":
    uv run artmind db refresh {{ table }} {{ if domain != "" { "--domain " + domain } else { "" } }}

# snapshot the structured store (parquet + registry) to a tar.gz  (usage: just db-backup)
db-backup:
    uv run artmind db backup

# wipe and restore the structured store from a snapshot  (usage: just db-restore [path])
db-restore path="":
    uv run artmind db restore {{ path }} --confirm

# ── artmind query ────────────────────────────────────────────────────────────

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

# retrieve all text chunks for a document  (usage: just query-graph-doc-chunks <domain> "document_name")
query-graph-doc-chunks domain document_name:
    uv run artmind query graph pattern10 --domain {{ domain }} --documentName "{{ document_name }}"

# structural metadata for a domain  (usage: just query-graph-structural <domain>)
query-graph-structural domain:
    uv run artmind query graph structural-metadata --domain {{ domain }}

# LLM-generated Cypher from natural language  (usage: just query-graph-text2cypher <domain> "question" [--dry-run])
query-graph-text2cypher domain question dry_run="":
    uv run artmind query graph text2cypher --domain {{ domain }} {{ if dry_run == "true" { "--dry-run" } else { "" } }} "{{ question }}"

# list materialized Conflict nodes for a domain  (usage: just query-graph-conflicts <domain> [flags])
query-graph-conflicts domain flags="":
    uv run artmind query graph conflicts --domain {{ domain }} {{ flags }}

# events/state-changes/supersessions for an entity, ordered by time  (usage: just query-graph-timeline <domain> <entity_id>)
query-graph-timeline domain entity_id:
    uv run artmind query graph timeline --domain {{ domain }} --entityId {{ entity_id }}

# per-domain routing summary: doc names/counts, entity counts, top classes
query-domains-overview:
    uv run artmind query domains-overview

# search chunks by vector + text (RRF combined)  (usage: just query-text <domain> "question")
query-text domain question top_k="5":
    uv run artmind query vector-text --domain {{ domain }} --topK {{ top_k }} "{{ question }}"

# resolve a name fragment or description to canonical graph entities  (usage: just query-entity-resolve <domain> "reference")
query-entity-resolve domain reference top_k="5":
    uv run artmind query entity-resolve --domain {{ domain }} --topK {{ top_k }} "{{ reference }}"

# fetch chunk text by exact id(s)  (usage: just query-chunks <domain> <chunk_id> [expand])
query-chunks domain chunk_id expand="0":
    uv run artmind query chunks --domain {{ domain }} --idList {{ chunk_id }} --expand {{ expand }}

# entity properties + one-hop relationships + source chunk text in one call  (usage: just query-entity-context <domain> <entity_id>)
query-entity-context domain entity_id:
    uv run artmind query entity-context --domain {{ domain }} --entityId {{ entity_id }}

# natural language to read-only DuckDB SQL against the structured store  (usage: just query-text2sql <domain> "question" [dry_run])
query-text2sql domain question dry_run="":
    uv run artmind query text2sql --domain {{ domain }} {{ if dry_run == "true" { "--dry-run" } else { "" } }} "{{ question }}"

# resolve a free-text value to a canonical column value and/or graph entity  (usage: just query-resolve-key <domain> "phrase" [column] [table])
query-resolve-key domain phrase column="" table="":
    uv run artmind query resolve-key --domain {{ domain }} {{ if column != "" { "--column " + column } else { "" } }} {{ if table != "" { "--table " + table } else { "" } }} "{{ phrase }}"

# ── artmind serve & web UIs ──────────────────────────────────────────────────

# start the warm query daemon in the background if not already up (logs to logs/serve.log)
serve-start:
    #!/usr/bin/env bash
    set -euo pipefail
    if curl -s -m 1 "http://127.0.0.1:{{SERVE_PORT}}/health" >/dev/null 2>&1; then
        echo "artmind serve already running on port {{SERVE_PORT}}"
    else
        mkdir -p logs
        nohup uv run artmind serve >> logs/serve.log 2>&1 &
        echo "artmind serve starting on port {{SERVE_PORT}} (pid $!) — logs/serve.log"
    fi

# stop the query daemon
serve-stop: (_free-port SERVE_PORT "artmind serve")

# restart the query daemon. Every daemon imports the code once at start, so a
# long-lived one keeps answering from the build it booted with — see CLAUDE.md
# "A running daemon serves stale code". This is why the UI recipes below restart
# rather than reuse it.
serve-restart: serve-stop serve-start

# start the query daemon plus the chat web UI (foreground; Ctrl-C stops the UI only)
serve-ui: serve-restart (_free-port CHAT_UI_PORT "artmind chat-ui")
    uv run artmind chat-ui

# start the query daemon plus the admin web UI (foreground; Ctrl-C stops the UI only)
serve-admin-ui: serve-restart (_free-port ADMIN_UI_PORT "artmind admin-ui")
    uv run artmind admin-ui

# ── shared primitive ─────────────────────────────────────────────────────────

# Stop the artmind daemon listening on {{port}} so a recipe can rebind it, then
# wait for the socket to clear. The one place this repo kills a daemon.
#
# Identifies the process by the port it holds (that's the actual conflict) and
# then confirms it really is artmind -- never string-matches a command line,
# which would also hit a shell or editor that merely mentions "artmind serve".
#
# strict="1" (default): a foreign process on the port is a hard error, because
#   the caller is about to bind that port and would otherwise fail confusingly.
# strict="0": warn and carry on, for callers that only want artmind's daemons
#   gone (dev-stop-daemons) and don't care who else is on the port.
_free-port port label strict="1":
    #!/usr/bin/env bash
    set -uo pipefail
    for pid in $(lsof -ti ":{{port}}" -sTCP:LISTEN 2>/dev/null || true); do
        case "$(ps -o command= -p "$pid" 2>/dev/null || true)" in
            *artmind*)
                echo "stopping {{label}} on port {{port}} (pid $pid)"
                kill "$pid" 2>/dev/null || true
                for _ in 1 2 3 4 5 6 7 8; do
                    kill -0 "$pid" 2>/dev/null || break
                    sleep 0.25
                done
                kill -0 "$pid" 2>/dev/null && { echo "  forcing pid $pid"; kill -9 "$pid" 2>/dev/null || true; }
                ;;
            *)
                if [ "{{strict}}" = "1" ]; then
                    echo "port {{port}} is held by a non-artmind process (pid $pid) — refusing to kill it" >&2
                    exit 1
                fi
                echo "port {{port}} held by a non-artmind process (pid $pid) — leaving it alone"
                ;;
        esac
    done
    # the socket can linger a moment after the process goes
    for _ in 1 2 3 4 5 6 7 8 9 10; do
        lsof -ti ":{{port}}" -sTCP:LISTEN >/dev/null 2>&1 || exit 0
        sleep 0.2
    done
    exit 0

# ── artmind session ──────────────────────────────────────────────────────────

# export Neo4j graph to a snapshot (end of session)
session-close:
    uv run artmind session close

# wipe Neo4j and restore from latest snapshot (start of session)
session-initiate:
    uv run artmind session initiate --yes

# ── artmind update ───────────────────────────────────────────────────────────

# extract facts and find graph candidates  (usage: just update-draft <domain> "text" [session])
update-draft domain text session="":
    uv run artmind update draft --domain {{ domain }} --text "{{ text }}" {{ if session != "" { "--session " + session } else { "" } }}

# write confirmed facts to Neo4j  (usage: just update-confirm <session> '<resolutions_json>')
update-confirm session resolutions:
    uv run artmind update confirm --session {{ session }} --resolutions '{{ resolutions }}'

# mark one entity node as superseding another (node-level supersession)  (usage: just update-supersede <newer_id> <older_id> [flags])
update-supersede newer older flags="":
    uv run artmind update supersede --newer {{ newer }} --older {{ older }} {{ flags }}

# list recent update sessions  (usage: just update-history [domain] [user] [limit])
update-history domain="" user="" limit="20":
    uv run artmind update history {{ if domain != "" { "--domain " + domain } else { "" } }} {{ if user != "" { "--user " + user } else { "" } }} --limit {{ limit }}

# export UserChat nodes to markdown files  (usage: just update-export [domain] [format] [output])
update-export domain="" fmt="sequential" output="data/chats":
    uv run artmind update export {{ if domain != "" { "--domain " + domain } else { "" } }} --format {{ fmt }} --output {{ output }}
