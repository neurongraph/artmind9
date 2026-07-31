import os
import shutil
import subprocess
import sys
import time
import json
from datetime import date, datetime
from pathlib import Path

import duckdb
import rich_click as click
import yaml
from loguru import logger

from artmind import graph_query, resolve_key, text2cypher, text2sql, vector_query
import artmind.update as update_backend
from artmind.graph_snapshot import export_graph, import_graph
from artmind.unified_snapshot import create_snapshot, analyze_snapshot, restore_snapshot_impl
from artmind.harmonizer import harmonize_all, harmonize_schema
from artmind.setup import scaffold_run_folder, setup_all
from artmind.ingest import (
    _build_file_result_from_db,
    clean_document,
    collect_ingest_files,
    commit_to_graph,
    embed_entities_backfill,
    extract_kg,
    ingest_file,
    ingest_to_kg,
)
from artmind.kg_pull import pull_kg as pull_kg_fn
from artmind.structured import is_structured_source, view_name
from artmind.structured import registry as structured_registry
from artmind.structured.scd2 import asof_view_sql
from artmind.structured.duckdb_adapter import DuckDBDatasource
from artmind.structured.pipeline import ingest_structured_file, refresh_table
from artmind.structured_snapshot import export_structured, import_structured
from artmind.jobs import (
    _create_job,
    _fetch_active_jobs,
    _fetch_chunks,
    _fetch_completed_jobs,
    _get_job_results,
    _get_job_status,
    _list_jobs,
    _retry_job,
)
from artmind.consolidate import consolidate_descriptions
from artmind.refine_graph import refine_graph
from artmind.refine_pipeline import run_pipeline
from paths import (
    DOMAIN_SCHEMAS_DIR,
    INGEST_LOG_FILE,
    REFINE_DIR,
    WORKER_LOG,
    WORKER_PID_FILE,
)
from utils.functions import load_env, resolve_llm_model


def _setup_logger(log_file: Path = INGEST_LOG_FILE) -> None:
    log_file.parent.mkdir(parents=True, exist_ok=True)
    logger.remove()
    logger.add(
        log_file,
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} [{level:<7}] {message}",
        level="DEBUG",
        rotation="10 MB",
        retention=5,
    )
    logger.add(
        sys.stderr,
        format="{time:YYYY-MM-DD HH:mm:ss} [{level:<7}] {message}",
        level="INFO",
    )


def _parse_domains(values: "tuple[str, ...]") -> list[str]:
    """Flatten repeatable/comma-split --domain values into a deduped list."""
    from artmind.graph_query import normalize_domains

    return normalize_domains(list(values))


# ── worker helpers ────────────────────────────────────────────────────────────


def _ensure_worker_running() -> None:
    if WORKER_PID_FILE.exists():
        try:
            pid = int(WORKER_PID_FILE.read_text().strip())
            os.kill(pid, 0)
            return  # live worker found
        except (ProcessLookupError, ValueError):
            pass  # stale PID

    # Locate the worker from the installed package (sibling of this module) —
    # never relative to the pid file, which lives in the data dir.
    worker_script = Path(__file__).resolve().parent / "worker.py"
    WORKER_LOG.parent.mkdir(parents=True, exist_ok=True)
    subprocess.Popen(
        [sys.executable, str(worker_script)],
        stdout=open(WORKER_LOG, "a"),
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    logger.info("Worker started in background")


# ── domain helpers ─────────────────────────────────────────────────────────────


def _load_domain_schema(domain_name: str) -> dict:
    domain_file = DOMAIN_SCHEMAS_DIR / f"{domain_name}_schema.yaml"
    if not domain_file.exists():
        raise click.ClickException(f"Domain schema not found: {domain_file}")
    with open(domain_file) as f:
        return yaml.safe_load(f)


def _get_available_domains() -> list[str]:
    """Return all domain names from schema files, always including 'general'."""
    domains = []
    if DOMAIN_SCHEMAS_DIR.exists():
        for df in sorted(DOMAIN_SCHEMAS_DIR.glob("*_schema.yaml")):
            try:
                with open(df) as f:
                    data = yaml.safe_load(f)
                name = data.get("name")
                if name and name not in domains:
                    domains.append(name)
            except Exception:
                pass
    if "general" not in domains:
        domains.append("general")
    return domains


def _prompt_for_domain() -> str:
    """Show available domains and prompt user to select one."""
    domains = _get_available_domains()
    click.echo("Available domains:")
    for d in domains:
        click.echo(f"  - {d}")
    return click.prompt(
        "Domain",
        default="general",
        type=click.Choice(domains, case_sensitive=False),
        show_choices=False,
    )


# ── CLI root ───────────────────────────────────────────────────────────────────

click.rich_click.TEXT_MARKUP = "rich"
click.rich_click.SHOW_ARGUMENTS = True
click.rich_click.GROUP_ARGUMENTS_OPTIONS = True
click.rich_click.STYLE_ERRORS_SUGGESTION = "magenta italic"
click.rich_click.MAX_WIDTH = 100

click.rich_click.COMMAND_GROUPS = {
    "artmind": [
        {"name": "Domains", "commands": ["domains"]},
        {"name": "Ingestion", "commands": ["ingest"]},
        {"name": "Structured store", "commands": ["db"]},
        {"name": "Query", "commands": ["query"]},
        {"name": "Documents", "commands": ["docs"]},
        {"name": "Updates", "commands": ["update"]},
        {"name": "Sessions", "commands": ["session", "snapshot"]},
        {"name": "Setup & tools", "commands": ["setup", "init", "serve", "chat-ui", "admin-ui"]},
    ],
    "artmind ingest": [
        {
            "name": "Sync & jobs",
            "commands": [
                "sync", "async", "jobs", "jobs-active", "jobs-completed",
                "job-status", "job-results", "job-chunks", "retry-job",
            ],
        },
        {
            "name": "Graph building",
            "commands": ["extract-kg", "write-to-graph", "pull-kg", "embed-entities"],
        },
        {
            "name": "Refinement",
            "commands": [
                "refine-graph",
                "refine-pipeline",
                "consolidate-descriptions",
                "normalize-time",
                "detect-conflicts",
                "resolve-conflict",
                "supersede",
                "detect-supersession",
            ],
        },
    ],
    "artmind db": [
        {"name": "Explore", "commands": ["bridge", "list", "schema", "catalogue"]},
        {"name": "Mappings & tuning", "commands": ["mappings", "propose", "grain"]},
        {"name": "Query", "commands": ["sql", "timeline"]},
        {"name": "Maintenance", "commands": ["refresh", "connect", "backup", "restore"]},
    ],
    "artmind query": [
        {"name": "Graph patterns", "commands": ["graph"]},
        {
            "name": "Lookups",
            "commands": [
                "domains-overview",
                "vector-text",
                "entity-resolve",
                "chunks",
                "entity-context",
                "text2sql",
                "resolve-key",
            ],
        },
    ],
}


@click.group()
def cli():
    """Artmind — a knowledge system that synchronizes with your mind."""
    pass


def _echo_json(payload: dict, compact: bool = False) -> None:
    # DuckDB DATE/TIMESTAMP/DECIMAL values arrive here straight off a result set
    # (`db sql`, `query text2sql`) and aren't JSON-serializable. `default=str`
    # renders them rather than crashing the CLI's only output path -- same call
    # this repo already makes for column profiles in `structured/pipeline.py`.
    kwargs = {"ensure_ascii": False, "default": str}
    if compact:
        kwargs["separators"] = (",", ":")
    else:
        kwargs["indent"] = 2
    click.echo(json.dumps(payload, **kwargs))


@cli.command()
@click.option("--host", default="127.0.0.1", show_default=True, help="Interface to bind.")
@click.option(
    "--port",
    default=None,
    type=int,
    help="Port to bind. Defaults to $ARTMIND_SERVE_PORT or 8377.",
)
def serve(host: str, port: int | None) -> None:
    """Run the warm query server; `artmind query` calls are proxied to it."""
    import uvicorn

    from artmind._entry import DEFAULT_PORT
    from artmind.server import create_app

    if port is None:
        port = int(os.environ.get("ARTMIND_SERVE_PORT", DEFAULT_PORT))
    click.echo(f"artmind serve listening on http://{host}:{port}")
    uvicorn.run(create_app(), host=host, port=port, log_level="warning")


@cli.command("chat-ui")
@click.option("--host", default="127.0.0.1", show_default=True, help="Interface to bind.")
@click.option("--port", default=8378, show_default=True, type=int, help="Port to bind.")
@click.option(
    "--acp-cmd",
    default=None,
    help="ACP agent command for the 'opencode (ACP)' backend "
    "[default: $ARTMIND_ACP_AGENT_CMD or 'opencode acp'].",
)
def chat_ui(host: str, port: int, acp_cmd: str | None) -> None:
    """Launch the artmind chat web UI (Claude Agent SDK or an ACP agent)."""
    from artmind.webui.app import run_chat_ui
    from artmind.webui.backends import set_acp_agent_cmd

    set_acp_agent_cmd(acp_cmd)
    click.echo(f"artmind chat UI on http://{host}:{port}")
    run_chat_ui(host=host, port=port)


@cli.command("admin-ui")
@click.option("--host", default="127.0.0.1", show_default=True, help="Interface to bind.")
@click.option("--port", default=8379, show_default=True, type=int, help="Port to bind.")
@click.option(
    "--acp-cmd",
    default=None,
    help="ACP agent command for the 'opencode (ACP)' backend "
    "[default: $ARTMIND_ACP_AGENT_CMD or 'opencode acp'].",
)
def admin_ui(host: str, port: int, acp_cmd: str | None) -> None:
    """Launch the artmind admin web UI (Claude Agent SDK or an ACP agent)."""
    from artmind.webui.app import run_admin_ui
    from artmind.webui.backends import set_acp_agent_cmd

    set_acp_agent_cmd(acp_cmd)
    click.echo(f"artmind admin UI on http://{host}:{port}")
    run_admin_ui(host=host, port=port)


# ── artmind domains ────────────────────────────────────────────────────────────


@cli.group()
def domains():
    """Manage domain schemas."""
    pass


@domains.command("list")
def list_domains():
    """List all available domain schemas, showing hierarchy."""
    all_domains = sorted(_get_available_domains())
    parents = [d for d in all_domains if '.' not in d]
    children = [d for d in all_domains if '.' in d]

    shown = set()
    for parent in parents:
        click.echo(parent)
        shown.add(parent)
        for child in children:
            if child.startswith(parent + '.'):
                click.echo(f"  {child}")
                shown.add(child)

    for child in children:
        if child not in shown:
            click.echo(child)


@domains.command("add")
@click.argument("domain_file", type=click.Path(exists=True))
def add_domain(domain_file: str):
    """Add a domain schema from a YAML file."""
    source = Path(domain_file)
    with open(source) as f:
        data = yaml.safe_load(f)
    domain_name = data.get("name")
    if not domain_name:
        raise click.ClickException("Domain YAML must contain a 'name' field")
    dest = DOMAIN_SCHEMAS_DIR / f"{domain_name}_schema.yaml"
    shutil.copy(source, dest)
    click.echo(f"Domain '{domain_name}' added to {dest}")


@domains.command("delete")
@click.argument("domain_name")
def delete_domain(domain_name: str):
    """Delete a domain schema."""
    domain_file = DOMAIN_SCHEMAS_DIR / f"{domain_name}_schema.yaml"
    if not domain_file.exists():
        raise click.ClickException(f"Domain schema not found: {domain_file}")
    domain_file.unlink()
    click.echo(f"Domain '{domain_name}' deleted")


@domains.command("entities-prompt")
@click.argument("domain_name")
def get_entities(domain_name: str):
    """The prompt used to extract entities from a document chunk"""
    data = _load_domain_schema(domain_name)
    click.echo(data.get("entities_prompt", []))


@domains.command("properties-prompt")
@click.argument("domain_name")
def get_properties(domain_name: str):
    """The prompt used to extract properties for the set of entities from a document chunk"""
    data = _load_domain_schema(domain_name)
    click.echo(data.get("properties_prompt", []))


@domains.command("relationships-prompt")
@click.argument("domain_name")
def get_relationships(domain_name: str):
    """The prompt used to extract relationships from a document chunk"""
    data = _load_domain_schema(domain_name)
    click.echo(data.get("relationships_prompt", []))


@domains.command("harmonize")
@click.option("--domain", default=None, help="Child domain to harmonize (e.g. fiction.thriller). Default: all children.")
@click.option("--dry-run", is_flag=True, help="Show what would change without writing.")
def domains_harmonize(domain: str | None, dry_run: bool):
    """Sync child domain schemas against their parent's entity types.

    Copies any entity, property, and relationship blocks that exist in the
    parent but are missing from the child. Never removes child-specific extras.
    """
    if domain:
        if '.' not in domain:
            raise click.ClickException(f"'{domain}' has no parent (no '.' in name). Only child domains can be harmonized.")
        results = [harmonize_schema(domain, dry_run=dry_run)]
    else:
        results = harmonize_all(dry_run=dry_run)

    if not results:
        click.echo("No child schemas found.")
        return

    for r in results:
        if r.status == 'error':
            click.echo(f"{r.domain}  ERROR: {r.error}")
        elif r.status == 'in_sync':
            click.echo(f"{r.domain}  already in sync")
        elif r.status == 'dry_run':
            click.echo(f"{r.domain}  would add entity types: {r.added}")
        else:
            click.echo(f"{r.domain}  added entity types: {r.added}")


# ── artmind ingest ─────────────────────────────────────────────────────────────


@cli.group()
def ingest():
    """Manage document ingestion (sync, async, status, results,...)."""
    pass


@ingest.command("sync")
@click.argument("file_path", type=click.Path(exists=True))
@click.option("--domain", default=None, help="Domain to assign (prompted if omitted)")
@click.option("--force", is_flag=True, help="Ingest even if identical content is already registered")
@click.option("--stage-only", is_flag=True, help="Extract KG JSON but do not write to the graph (leaves it staged for a later commit)")
@click.option(
    "--refreshMode", "refresh_mode",
    type=click.Choice(["replace", "temporal"]), default="replace",
    help="Structured (csv/xlsx) files only: replace (default) overwrites the table on re-ingest;"
    " temporal keeps full SCD-2 history (requires --businessKey). Ignored for KG documents.",
)
@click.option(
    "--businessKey", "business_key", default=None,
    help="Structured files only: business-key column(s) identifying the same logical row across"
    " refreshes, comma-splittable (e.g. 'id' or 'id,region'). Required with --refreshMode temporal.",
)
@click.option(
    "--effectiveDateColumn", "effective_date_column", default=None,
    help="Structured files only: column supplying each row's effective date for temporal refresh"
    " (falls back to the ingest date when omitted or null on a row).",
)
def ingest_sync(
    file_path: str,
    domain: str | None,
    force: bool,
    stage_only: bool,
    refresh_mode: str,
    business_key: str | None,
    effective_date_column: str | None,
):
    """Ingest a file or directory synchronously (blocking)."""
    _setup_logger()
    env = load_env()
    image_model = env.get("ARTMIND_IMAGE_MODEL", "gemma4:e4b")
    text_model = resolve_llm_model(env)
    embed_model = env.get("ARTMIND_KG_EMBEDDINGS_MODEL", "nomic-embed-text:latest")
    chunk_size = int(env.get("ARTMIND_KG_CHUNK_SIZE", "6000"))

    if domain is None:
        domain = _prompt_for_domain()
    elif domain not in _get_available_domains():
        raise click.ClickException(
            f"Unknown domain '{domain}'. Run 'artmind domains list' to see available domains."
        )

    path = Path(file_path)
    files = collect_ingest_files(path)

    logger.info(
        "═══ Sync ingest: {} file(s) | domain={} | image_model={} | text_model={} | embed={} | chunk_size={}",
        len(files),
        domain,
        image_model,
        text_model,
        embed_model,
        chunk_size,
    )
    for name in (f.name for f in files):
        logger.debug("  File: {}", name)
    t_batch = time.monotonic()
    ok_count, fail_count = 0, 0
    for f in files:
        try:
            if is_structured_source(f):
                res = ingest_structured_file(
                    f,
                    domain,
                    force=force,
                    refresh_mode=refresh_mode,
                    business_key=business_key,
                    effective_date_column=effective_date_column,
                )
                ok_count += 1 if res.get("status") == "ok" else 0
                continue
            result = ingest_file(f, image_model, domain, chunk_size=chunk_size, force=force)
            if result.get("status") == "ok":
                kg_ok = ingest_to_kg(result, domain, text_model, embed_model, chunk_size, stage_only=stage_only)
                if kg_ok:
                    ok_count += 1
                else:
                    fail_count += 1
                    logger.error("KG ingestion failed for {}", f.name)
            else:
                fail_count += 1
        except Exception as e:
            fail_count += 1
            logger.error("Unexpected error processing {}: {}", f, e)
    elapsed = time.monotonic() - t_batch
    logger.info(
        "═══ Sync ingest complete in {:.1f}s — ok={}, failed={}",
        elapsed,
        ok_count,
        fail_count,
        )


@ingest.command("async")
@click.argument("file_path", type=click.Path(exists=True))
@click.option("--domain", default=None, help="Domain to assign (prompted if omitted)")
@click.option("--force", is_flag=True, help="Ingest even if identical content is already registered")
@click.option("--stage-only", is_flag=True, help="Extract KG JSON but do not write to the graph (leaves it staged for a later commit)")
def ingest_async(file_path: str, domain: str | None, force: bool, stage_only: bool):
    """Submit a file or directory for background ingestion; returns job_id immediately."""
    _setup_logger()
    if domain is None:
        domain = _prompt_for_domain()
    elif domain not in _get_available_domains():
        raise click.ClickException(
            f"Unknown domain '{domain}'. Run 'artmind domains list' to see available domains."
        )

    path = Path(file_path)
    files = collect_ingest_files(path)
    if not files:
        raise click.ClickException(f"No files found in {path}")

    batch_files = [str(f.resolve()) for f in files]
    job_id = _create_job(batch_files, domain=domain, force=force, stage_only=stage_only)
    _ensure_worker_running()

    _echo_json({
        "job_id": job_id,
        "domain": domain,
        "file_count": len(batch_files),
        "submitted_at": datetime.now().isoformat(),
        "message": f"Job submitted with {len(batch_files)} file(s)",
    })


@ingest.command("jobs")
@click.option("--status", default=None, help="Filter by status (queued/processing/completed/failed)")
@click.option("--compact", is_flag=True, help="Emit compact JSON")
def ingest_jobs(status: str | None, compact: bool):
    """List recent ingestion jobs."""
    jobs = _list_jobs(status_filter=status)
    _echo_json(jobs, compact)


@ingest.command("job-status")
@click.argument("job_id")
@click.option("--compact", is_flag=True, help="Emit compact JSON")
def ingest_job_status(job_id: str, compact: bool):
    """Show status and per-file progress for a job."""
    result = _get_job_status(job_id)
    if result is None:
        raise click.ClickException(f"Job '{job_id}' not found")
    _echo_json(result, compact)


@ingest.command("job-results")
@click.argument("job_id")
@click.option("--compact", is_flag=True, help="Emit compact JSON")
def ingest_job_results(job_id: str, compact: bool):
    """Show detailed per-file results for a completed job."""
    result = _get_job_results(job_id)
    if result is None:
        raise click.ClickException(f"Job '{job_id}' not found")
    _echo_json(result, compact)


@ingest.command("retry-job")
@click.argument("job_id")
@click.option("--include-skipped", is_flag=True, help="Also re-queue files that were skipped as duplicates")
def ingest_retry_job(job_id: str, include_skipped: bool):
    """Re-queue failed files in a job for reprocessing.

    Removes failed files from the document registry and resets the job to queued
    so the worker picks it up again. Use --include-skipped to also force
    re-processing of files that were skipped as duplicates.
    """
    try:
        result = _retry_job(job_id, include_skipped=include_skipped)
    except ValueError as e:
        raise click.ClickException(str(e))
    if result["retried"] == 0:
        click.echo(f"No files to retry in job '{job_id}'")
    else:
        click.echo(f"Job '{job_id}' re-queued ({result['domain']}): {result['retried']} file(s) reset, {result['deregistered']} removed from registry")
        for f in result["files"]:
            click.echo(f"  {f}")
        _ensure_worker_running()
        click.echo("Worker started.")


@ingest.command("jobs-active")
@click.option("--compact", is_flag=True, help="Emit compact JSON")
def ingest_jobs_active(compact: bool):
    """List queued/processing jobs with full per-file rows."""
    _echo_json(_fetch_active_jobs(), compact)


@ingest.command("jobs-completed")
@click.option("--limit", default=100, help="Max jobs to return")
@click.option("--compact", is_flag=True, help="Emit compact JSON")
def ingest_jobs_completed(limit: int, compact: bool):
    """List completed/failed jobs with a per-file summary."""
    _echo_json(_fetch_completed_jobs(limit=limit), compact)


@ingest.command("job-chunks")
@click.argument("job_id")
@click.argument("document")
@click.option("--compact", is_flag=True, help="Emit compact JSON")
def ingest_job_chunks(job_id: str, document: str, compact: bool):
    """Show per-chunk extraction status (entities/properties/relationships) for one document in a job."""
    job = _get_job_status(job_id)
    if job is None:
        raise click.ClickException(f"Job '{job_id}' not found")
    file_result = _build_file_result_from_db(document, job["domain"])
    if file_result is None:
        raise click.ClickException(f"Document '{document}' not found in registry for domain '{job['domain']}'")
    _echo_json(_fetch_chunks(file_result["sha256"]), compact)


@ingest.command("embed-entities")
@click.option("--domain", required=True, help="Domain to backfill")
@click.option("--compact", is_flag=True, help="Emit compact JSON")
def ingest_embed_entities(domain: str, compact: bool) -> None:
    """Backfill vector embeddings for entities missing one (powers query entity-resolve)."""
    _echo_json(embed_entities_backfill(domain), compact)


@ingest.command("extract-kg")
@click.argument("document_name")
@click.option("--domain", required=True, help="Domain the document belongs to")
@click.option("--maxWorkers", "max_workers", type=int, default=None,
              help="Chunk-level concurrency (default: 4 for openrouter, 1 for ollama; "
                   "or ARTMIND_INGEST_MAX_WORKERS)")
def ingest_extract_kg(document_name: str, domain: str, max_workers: int | None) -> None:
    """Re-run or resume KG extraction for a document (skips already-ok chunks).

    DOCUMENT_NAME is the registered filename (e.g. myfile.pdf).
    Extraction results are merged into doc-level JSON files ready for write_to_graph.
    """
    _setup_logger()
    env = load_env()
    text_model = resolve_llm_model(env)
    embed_model = env.get("ARTMIND_KG_EMBEDDINGS_MODEL", "nomic-embed-text:latest")

    file_result = _build_file_result_from_db(document_name, domain)
    if file_result is None:
        raise click.ClickException(f"Document '{document_name}' not found in registry for domain '{domain}'")
    if file_result["chunk_count"] == 0:
        raise click.ClickException(
            f"No chunks found at {file_result['chunks_dir']} — "
            "run 'ingest sync' first to split and register the document"
        )

    logger.info("extract_kg: {} (domain={}) — {} chunk(s)", document_name, domain, file_result["chunk_count"])
    doc_kg_dir = extract_kg(file_result, domain, text_model, embed_model, max_workers=max_workers)
    if doc_kg_dir:
        logger.info("extract_kg complete — merged JSON in {}", doc_kg_dir)
    else:
        raise click.ClickException("extract_kg failed — check logs for details")


@ingest.command("write-to-graph")
@click.argument("document_name", required=False, default=None)
@click.option("--domain", default=None, help="Domain the document belongs to (required for single-document mode)")
@click.option("--folder", type=click.Path(exists=True), default=None, help="Path to folder whose sub-folders are document KG directories")
def ingest_write_to_graph(document_name: str | None, domain: str | None, folder: str | None) -> None:
    """Write already-extracted KG JSON to Neo4j (re-run after fixing Neo4j issues).

    \b
    Two modes:
      Single document:  artmind ingest write-to-graph DOCUMENT_NAME --domain DOMAIN
      Batch from folder: artmind ingest write-to-graph --folder PATH [--domain DOMAIN]

    In single-document mode DOCUMENT_NAME is the registered filename (e.g. myfile.pdf).
    In folder mode each immediate sub-folder of PATH that contains a document.json is
    written to Neo4j. If --domain is omitted in folder mode, the domain is inferred
    from the parent directory name (i.e. PATH is expected to be data/kg/<domain>).
    """
    _setup_logger()
    from paths import KG_DIR

    if folder and document_name:
        raise click.ClickException("Provide either DOCUMENT_NAME or --folder, not both")
    if not folder and not document_name:
        raise click.ClickException("Provide DOCUMENT_NAME or --folder")

    # ── single-document mode ──────────────────────────────────────────────
    if document_name:
        if not domain:
            raise click.ClickException("--domain is required when specifying a single document")
        file_result = _build_file_result_from_db(document_name, domain)
        if file_result is None:
            raise click.ClickException(f"Document '{document_name}' not found in registry for domain '{domain}'")

        registered_path = Path(file_result["registered_path"])
        doc_kg_dir = KG_DIR / domain / registered_path.stem
        if not (doc_kg_dir / "document.json").exists():
            raise click.ClickException(
                f"Merged KG JSON not found in {doc_kg_dir} — run 'ingest extract_kg' first"
            )

        logger.info("write_to_graph: {} (domain={}) from {}", document_name, domain, doc_kg_dir)
        ok = commit_to_graph(doc_kg_dir, domain)
        if ok:
            logger.info("write_to_graph complete")
        else:
            raise click.ClickException("write_to_graph failed — check logs for Neo4j errors")
        return

    # ── folder (batch) mode ───────────────────────────────────────────────
    folder_path = Path(folder)
    if not folder_path.is_dir():
        raise click.ClickException(f"{folder} is not a directory")

    resolved_domain = domain or folder_path.name
    doc_dirs = sorted(
        d for d in folder_path.iterdir()
        if d.is_dir() and (d / "document.json").exists()
    )
    if not doc_dirs:
        # Fall back to recursive search
        doc_dirs = sorted(
            p.parent for p in folder_path.rglob("document.json")
        )
        if not doc_dirs:
            raise click.ClickException(f"No document sub-folders with document.json found in {folder_path}")

        click.echo(f"\nNo document.json found in immediate sub-folders. Found {len(doc_dirs)} document(s) recursively:\n")
        for d in doc_dirs:
            click.echo(f"  {d.relative_to(folder_path)}")
        click.echo()
        if not click.confirm(f"Write all {len(doc_dirs)} document(s) to graph (domain={resolved_domain})?"):
            raise click.Abort()

    logger.info(
        "write_to_graph (batch): {} document(s) in {} (domain={})",
        len(doc_dirs), folder_path, resolved_domain,
    )

    ok_count, fail_count = 0, 0
    for doc_kg_dir in doc_dirs:
        logger.info("  writing {} …", doc_kg_dir.name)
        try:
            if commit_to_graph(doc_kg_dir, resolved_domain):
                ok_count += 1
            else:
                fail_count += 1
                logger.error("  FAILED: {}", doc_kg_dir.name)
        except Exception as e:
            fail_count += 1
            logger.error("  FAILED: {} — {}", doc_kg_dir.name, e)

    logger.info(
        "write_to_graph batch complete: {}/{} succeeded, {} failed",
        ok_count, len(doc_dirs), fail_count,
    )
    if fail_count:
        raise click.ClickException(f"{fail_count} document(s) failed — check logs for details")



@ingest.command("pull-kg")
@click.option("--repo", required=True, help="Git-cloneable URL (SSH or HTTPS) of the external repository")
@click.option("--repo-path", required=True, help="Path within the repo to the folder containing document sub-folders")
@click.option("--domain", required=True, help="Target domain name")
def ingest_pull_kg(repo: str, repo_path: str, domain: str) -> None:
    """Pull KG JSON from an external GitHub repo into the local KG directory.

    \b
    Uses sparse git checkout to fetch only the specified sub-path.
    Each immediate sub-folder containing a document.json is copied into data/kg/<domain>/.
    Aborts if any sub-folder already exists locally (resolve conflicts manually first).

    Example:
      artmind ingest pull-kg \\
        --repo git@github.com:acme/kg-store.git \\
        --repo-path data/kg/sales_collateral \\
        --domain sales_collateral
    """
    _setup_logger()
    try:
        result = pull_kg_fn(repo, repo_path, domain)
    except RuntimeError as e:
        raise click.ClickException(str(e))
    logger.info(
        "pull-kg complete: {} document(s) pulled into domain '{}'",
        result["pulled_count"], result["domain"],
    )


@ingest.command("refine-graph")
@click.option("--domain", default=None, help="Restrict to entities in this domain (default: all domains)")
@click.option(
    "--filter",
    "name_filter",
    default=None,
    help="Filter entities by name (comma-separated for multiple). Default: all entities in domain",
)
@click.option(
    "--model",
    default=None,
    help="LLM model for merge decisions (default: ARTMIND_KG_LLM_MODEL env var)",
)
@click.option(
    "--threshold",
    type=float,
    default=0.7,
    show_default=True,
    help="String similarity threshold for clustering (0–1)",
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Compute and write proposals only; do NOT apply merges to Neo4j",
)
@click.option(
    "--output",
    "output_file",
    default=None,
    type=click.Path(),
    help="Write proposals JSON to this file (default: data/refine/proposed_merges.json)",
)
@click.option(
    "--from-file",
    "from_file",
    default=None,
    type=click.Path(exists=True),
    help="Skip computation — load proposals from a previous dry-run JSON file and apply them",
)
@click.option(
    "--allow-cross-domain-merge",
    "allow_cross_domain_merge",
    is_flag=True,
    default=False,
    help=(
        "Allow merging same-named entities across domains (default: skip and report them). "
        "Only applies when computing proposals via clustering; has no effect with --from-file, "
        "which applies proposals already vetted at dry-run time."
    ),
)
def ingest_refine_graph(
    domain: str | None,
    name_filter: str | None,
    model: str | None,
    threshold: float,
    dry_run: bool,
    output_file: str | None,
    from_file: str | None,
    allow_cross_domain_merge: bool,
) -> None:
    """Find similar entity names, merge aliases into canonical entities.

    \b
    Workflow:
      1. Dry-run:  artmind ingest refine-graph --dry-run --output merges.json
      2. Review merges.json and edit if needed
      3. Execute: artmind ingest refine-graph --from-file merges.json
    """
    _setup_logger()
    env = load_env()
    resolved_model = resolve_llm_model(env, model)

    out_path: Path | None = None
    if from_file is None:
        if output_file:
            out_path = Path(output_file)
        else:
            REFINE_DIR.mkdir(parents=True, exist_ok=True)
            out_path = REFINE_DIR / "proposed_merges.json"
    elif output_file:
        out_path = Path(output_file)

    from_path = Path(from_file) if from_file else None

    report = refine_graph(
        domain=domain,
        name_filter=name_filter,
        model=resolved_model,
        similarity_threshold=threshold,
        dry_run=dry_run,
        output_file=out_path,
        from_file=from_path,
        allow_cross_domain_merge=allow_cross_domain_merge,
    )

    proposed = report.get("proposed_merges", {})
    stats = report.get("stats", {})

    if from_path or not dry_run:
        click.echo(f"Merges applied — merged={stats.get('merged',0)} skipped={stats.get('skipped',0)} errors={stats.get('errors',0)}")
    else:
        click.echo(f"Dry-run complete — {len(proposed)} merge(s) proposed")
        if out_path:
            click.echo(f"Proposals written to: {out_path}")
            click.echo(f"To apply: artmind ingest refine-graph --from-file {out_path}")

    skipped = report.get("skipped_cross_domain", {})
    if skipped:
        click.echo(f"Skipped {len(skipped)} cross-domain merge cluster(s) (use --allow-cross-domain-merge to merge): {skipped}")


@ingest.command("refine-pipeline")
@click.option("--domain", "domain", required=True, multiple=True, help="Domain to refine (repeatable; 2+ domains add a cross-domain conflicts pass)")
@click.option("--apply", "apply_", is_flag=True, help="One-shot: compute AND apply every step (skips the review gate)")
@click.option("--from-file", "from_file", default=None, type=click.Path(exists=True), help="Apply vetted proposals from a prior propose report (pipeline_report.json)")
@click.option("--steps", default=None, help="Comma-separated subset of: time,supersession,merge,conflicts,consolidate,embed (canonical order enforced)")
@click.option("--model", default=None, help="LLM model for merge/conflict/consolidation calls (default: env)")
@click.option("--mergeThreshold", "merge_threshold", type=float, default=0.7, show_default=True, help="Similarity threshold for merge clustering")
@click.option("--simThreshold", "conflict_sim_threshold", type=float, default=0.75, show_default=True, help="Similarity threshold for conflict candidate pairs")
@click.option("--maxPairs", "max_pairs", type=int, default=200, show_default=True, help="Cap on conflict candidate pairs per detection pass (bounds LLM cost)")
@click.option("--sampleConsolidations", "sample_consolidations", type=int, default=3, show_default=True, help="Consolidation samples shown per domain in propose mode")
@click.option("--consolidateLimit", "consolidate_limit", type=int, default=None, help="Cap entities consolidated per domain in apply mode (default: all)")
@click.option("--compact", is_flag=True, help="Emit compact JSON")
def ingest_refine_pipeline(
    domain: tuple,
    apply_: bool,
    from_file: str | None,
    steps: str | None,
    model: str | None,
    merge_threshold: float,
    conflict_sim_threshold: float,
    max_pairs: int,
    sample_consolidations: int,
    consolidate_limit: int | None,
    compact: bool,
) -> None:
    """Run all refinement steps in dependency order: time → supersession → merge → conflicts → consolidate → embed.

    \b
    Workflow:
      1. Propose:  artmind ingest refine-pipeline --domain <d> [--domain <d2>]
         (time/supersession run for real — additive and idempotent;
          merge/conflicts/consolidation produce reviewable proposals;
          with 2+ domains, a cross-domain conflicts pass runs after every
          domain's own steps — merges land first by construction)
      2. Review the report and its merges_*.json / conflicts_*.json; edit if needed
      3. Apply:    artmind ingest refine-pipeline --domain <d> --from-file <report>
    Use --apply to skip the review gate (trusted automation only).
    """
    _setup_logger()
    step_list = steps.split(",") if steps else None
    try:
        result = run_pipeline(
            domains=_parse_domains(domain),
            apply=apply_,
            from_file=from_file,
            steps=step_list,
            model=model,
            merge_threshold=merge_threshold,
            conflict_sim_threshold=conflict_sim_threshold,
            max_pairs=max_pairs,
            sample_consolidations=sample_consolidations,
            consolidate_limit=consolidate_limit,
        )
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    _echo_json(result, compact)


@ingest.command("consolidate-descriptions")
@click.option("--domain", required=True, help="Domain to consolidate (sub-domains rolled up)")
@click.option("--nameFilter", "name_filter", default=None, help="Restrict to entities whose name contains this")
@click.option("--minFragments", "min_fragments", type=int, default=2, show_default=True, help="Skip never-consolidated entities with fewer accumulated description fragments")
@click.option("--maxChunks", "max_chunks", type=int, default=6, show_default=True, help="Max source chunk excerpts fed to the LLM per entity")
@click.option("--limit", type=int, default=None, help="Cap entities consolidated this run (bounds LLM cost)")
@click.option("--model", default=None, help="Consolidation LLM model (default: env)")
@click.option("--force", is_flag=True, help="Re-consolidate even if the chunk set is unchanged")
@click.option("--dry-run", is_flag=True, help="Generate proposals without writing to the graph")
@click.option("--compact", is_flag=True, help="Emit compact JSON")
def ingest_consolidate_descriptions(
    domain: str,
    name_filter: str | None,
    min_fragments: int,
    max_chunks: int,
    limit: int | None,
    model: str | None,
    force: bool,
    dry_run: bool,
    compact: bool,
) -> None:
    """Rewrite accumulated entity descriptions into clean prose from their source chunks.

    Preserves the original in description_raw, records provenance in
    description_source_chunks/description_source_docs, skips entities with open
    conflicts, and re-embeds rewritten entities.
    """
    _setup_logger()
    result = consolidate_descriptions(
        domain=domain,
        name_filter=name_filter,
        min_fragments=min_fragments,
        max_chunks=max_chunks,
        limit=limit,
        model=model,
        force=force,
        dry_run=dry_run,
    )
    _echo_json(result, compact)


@ingest.command("normalize-time")
@click.option("--domain", required=True, help="Domain to backfill canonical temporal properties for")
@click.option("--dry-run", is_flag=True, help="Compute counts only; do not write")
@click.option("--compact", is_flag=True, help="Emit compact JSON")
def ingest_normalize_time(domain: str, dry_run: bool, compact: bool) -> None:
    """Backfill canonical valid_from/valid_to/event_at from schema temporal mappings.

    Additive and idempotent. Runs automatically per document at ingest; use this
    to backfill pre-existing documents or after editing a schema's temporal block.
    """
    _setup_logger()
    from artmind.temporal import normalize_time
    _echo_json(normalize_time(domain, dry_run=dry_run), compact)


@ingest.command("detect-conflicts")
@click.option("--domain", "domain", required=True, multiple=True, help="Target domain(s) (repeatable; 1=intra-domain, 2+=cross-domain)")
@click.option("--nameFilter", "name_filter", default=None, help="Restrict to entities whose name contains this")
@click.option("--simThreshold", "sim_threshold", type=float, default=0.75, show_default=True, help="Min cosine similarity for a candidate")
@click.option("--maxPairs", "max_pairs", type=int, default=200, show_default=True, help="Hard cap on candidate pairs (bounds LLM cost)")
@click.option("--maxChunksPerSide", "max_chunks", type=int, default=2, show_default=True, help="Evidence chunks per side")
@click.option("--model", default=None, help="Adjudication LLM model (default: env)")
@click.option("--dry-run", is_flag=True, help="Compute proposals + write --output; do NOT materialize")
@click.option("--output", "output_file", default=None, type=click.Path(), help="Write proposals JSON here")
@click.option("--from-file", "from_file", default=None, type=click.Path(exists=True), help="Materialize proposals from a prior dry-run file")
@click.option("--compact", is_flag=True, help="Emit compact JSON")
def ingest_detect_conflicts(
    domain: tuple,
    name_filter: str | None,
    sim_threshold: float,
    max_pairs: int,
    max_chunks: int,
    model: str | None,
    dry_run: bool,
    output_file: str | None,
    from_file: str | None,
    compact: bool,
) -> None:
    """Detect non-destructive conflicts between entities across domains.

    \b
    Precondition: run intra-domain `refine-graph` (no --allow-cross-domain-merge)
    on each target domain first, so pairing operates on deduplicated entities.
    Workflow:
      1. artmind ingest detect-conflicts --domain A --domain B --dry-run --output conflicts.json
      2. Review conflicts.json
      3. artmind ingest detect-conflicts --domain A --domain B --from-file conflicts.json
    """
    _setup_logger()
    from artmind.conflicts import detect_conflicts
    env = load_env()
    resolved_model = resolve_llm_model(env, model)
    domains = _parse_domains(domain)
    report = detect_conflicts(
        domains=domains, name_filter=name_filter, sim_threshold=sim_threshold,
        max_pairs=max_pairs, max_chunks_per_side=max_chunks, model=resolved_model,
        dry_run=dry_run, output_file=Path(output_file) if output_file else None,
        from_file=Path(from_file) if from_file else None,
    )
    _echo_json(report, compact)


@ingest.command("resolve-conflict")
@click.argument("conflict_id")
@click.option("--status", type=click.Choice(["resolved", "dismissed"]), required=True, help="How this conflict was closed")
@click.option("--reason", default=None, help="Why — recorded on the Conflict node for audit")
@click.option("--compact", is_flag=True, help="Emit compact JSON")
def ingest_resolve_conflict(conflict_id: str, status: str, reason: str | None, compact: bool) -> None:
    """Close a materialized conflict as resolved or dismissed.

    Detection never closes conflicts on its own — two authorities disagreeing is
    a human judgment call. Use `query graph conflicts --status all` to see closed
    ones afterwards.
    """
    _setup_logger()
    from artmind.conflicts import resolve_conflict
    try:
        result = resolve_conflict(conflict_id, status, reason=reason)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    _echo_json(result, compact)


@ingest.command("supersede")
@click.option("--domain", required=True, help="Domain of both documents")
@click.option("--newer", "newer_name", required=True, help="Newer document name")
@click.option("--older", "older_name", required=True, help="Superseded document name")
@click.option("--scope", type=click.Choice(["document", "section", "clause"]), default="document", show_default=True)
@click.option("--effective", default=None, help="ISO date the supersession takes effect")
@click.option("--compact", is_flag=True, help="Emit compact JSON")
def ingest_supersede(domain: str, newer_name: str, older_name: str, scope: str, effective: str | None, compact: bool) -> None:
    """Manually assert that one document supersedes another (sets SUPERSEDES + valid_to)."""
    if scope != "document":
        raise click.ClickException(
            f"--scope {scope} is not yet supported. Sub-document supersession needs "
            "section/clause units the graph does not model, and applying it today "
            "would retire the whole document while leaving its chunks live. "
            "Use --scope document."
        )
    _setup_logger()
    from artmind.temporal import apply_supersession
    from artmind.graph_query import neo4j_session
    with neo4j_session() as session:
        rows = session.run(
            "MATCH (d:Document) WHERE d.domain=$domain AND d.name IN [$n,$o] RETURN d.name AS name, d.id AS id",
            domain=domain, n=newer_name, o=older_name,
        ).data()
    by_name: dict[str, list[str]] = {}
    for r in rows:
        by_name.setdefault(r["name"], []).append(r["id"])
    missing = [n for n in (newer_name, older_name) if n not in by_name]
    if missing:
        raise click.ClickException(
            f"Could not resolve document(s) {missing} in domain {domain} (found: {list(by_name)})"
        )
    ambiguous = {n: ids for n, ids in by_name.items() if len(ids) > 1}
    if ambiguous:
        raise click.ClickException(
            f"Ambiguous document name(s) in domain {domain} — multiple Document nodes share "
            f"the same name (e.g. from re-ingesting an edited file): {ambiguous}. "
            "Resolve manually before superseding."
        )
    _echo_json(
        apply_supersession(by_name[newer_name][0], by_name[older_name][0], scope, effective),
        compact,
    )


@ingest.command("detect-supersession")
@click.option("--domain", required=True, help="Domain to scan for supersession declarations (notices, metadata rows, and — when the schema enables supersede_on_title_family — title-family version chains)")
@click.option("--dry-run", is_flag=True, help="Report matches without writing")
@click.option("--compact", is_flag=True, help="Emit compact JSON")
def ingest_detect_supersession(domain: str, dry_run: bool, compact: bool) -> None:
    """Scan documents for supersession declarations and apply SUPERSEDES edges.

    Recognizes prose "## Supersession Notice" sections, metadata-table
    "| Supersedes | [[doc]] |" rows, and — when the domain schema sets
    temporal.defaults.supersede_on_title_family — inferred version chains among
    same-title-family documents ordered by valid_from.
    """
    _setup_logger()
    from artmind.temporal import detect_supersession
    _echo_json(detect_supersession(domain, dry_run=dry_run), compact)


# ── artmind query ──────────────────────────────────────────────────────────────


@cli.group()
def db():
    """Manage and read the structured (SQL) store: bridge/list/schema/sql/grain/propose/mappings/catalogue/refresh/connect/backup/restore.

    `db bridge` is the routing entry point — it answers whether a structured
    store exists for a domain, which tables are about which entity classes, and
    which columns' values are worth searching the graph for.
    """
    pass


@db.command("list")
@click.option("--domain", "domain", multiple=True, help="Domain(s) to scope (repeatable; comma-splittable). Omit for all.")
@click.option("--compact", is_flag=True, help="Emit compact JSON")
def db_list(domain, compact):
    """List registered structured tables, optionally domain-scoped."""
    domains = _parse_domains(domain) if domain else None
    _echo_json({"tables": structured_registry.list_tables(domains)}, compact)


@db.command("schema")
@click.argument("table", required=False)
@click.option("--domain", "domain", multiple=True, help="Domain(s) to scope (repeatable; comma-splittable). Omit for all.")
@click.option("--compact", is_flag=True, help="Emit compact JSON")
def db_schema(table, domain, compact):
    """Show columns/types (+ profiles/mappings once populated) — the context an LLM needs to write SQL."""
    domains = _parse_domains(domain) if domain else None
    tables = structured_registry.list_tables(domains)
    if table:
        tables = [t for t in tables if t["table_name"] == table]
        if not tables:
            raise click.ClickException(f"table '{table}' not found")
    result = []
    for t in tables:
        columns = structured_registry.get_columns(t["id"])
        mappings = structured_registry.list_mappings(t["id"])
        result.append({**t, "columns": columns, "mappings": mappings})
    _echo_json({"tables": result}, compact)


@db.command("sql")
@click.argument("sql")
@click.option("--compact", is_flag=True, help="Emit compact JSON")
def db_sql(sql, compact):
    """Run raw read-only SQL against the structured store — no LLM (the independent-query guarantee)."""
    try:
        text2sql.validate_read_only_sql(sql)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    ds = DuckDBDatasource()
    ds.ensure_views(structured_registry.list_tables())
    try:
        rows = ds.run_sql(sql)
    except Exception as exc:
        raise click.ClickException(f"SQL error: {exc}") from exc
    _echo_json({"query_type": "sql", "command": "db sql", "rows": rows}, compact)


@db.command("timeline")
@click.argument("table")
@click.option("--domain", "domain", multiple=True, help="Domain(s) to scope table resolution (repeatable; comma-splittable).")
@click.option("--asOf", "as_of", default=None, help="ISO date to view TABLE's rows as of (omit for the currently-open rows).")
@click.option("--compact", is_flag=True, help="Emit compact JSON")
def db_timeline(table, domain, as_of, compact):
    """Point-in-time query over a temporal (SCD-2) table's captured history.

    Only applies to tables ingested with --refreshMode temporal (2.7) — a
    replace-mode table keeps no history to query. Omit --asOf for the rows
    currently open (not yet superseded by a later refresh).
    """
    row = _resolve_table_row(table, domain)
    if row["refresh_mode"] != "temporal":
        raise click.ClickException(
            f"table '{table}' has refresh_mode='{row['refresh_mode']}' — timeline queries only"
            " apply to refresh_mode='temporal' tables (see 'artmind ingest sync --refreshMode temporal')"
        )
    ds = DuckDBDatasource()
    ds.ensure_views(structured_registry.list_tables())
    view = view_name(row["domain"], row["table_name"])
    try:
        rows = ds.run_sql(asof_view_sql(view, as_of))
    except Exception as exc:
        raise click.ClickException(f"SQL error: {exc}") from exc
    _echo_json(
        {"table": table, "domain": row["domain"], "as_of": as_of, "rows": rows}, compact
    )


class _PeeledArgument(click.Argument):
    """An Argument whose value is consumed by the group, not by Click's parser.

    ``consume_value`` normally reads only from the parser's ``opts``, so an
    Argument excluded from the parser (see ``_TableFirstGroup.make_parser``)
    always looks absent — which forced it to be declared ``required=False``
    even though it is mandatory. That lie propagated: both ``--help`` and the
    admin-ui CLI guide rendered ``[TABLE]`` with "no required args", while
    omitting it actually errors.

    Reading the value the group already peeled into ``ctx.params`` lets the
    Argument be declared honestly as ``required=True``.
    """

    def consume_value(self, ctx: click.Context, opts):
        if self.name in ctx.params:
            return ctx.params[self.name], click.core.ParameterSource.COMMANDLINE
        return super().consume_value(ctx, opts)


class _TableFirstGroup(click.RichGroup):
    """Group whose first positional token is always TABLE.

    Click's MultiCommand disables interspersed-argument parsing (it has to, to
    know where a subcommand name begins), which means a Group's own
    ``@click.argument`` can't have trailing Options when no subcommand follows —
    any token after the argument gets misread as an attempted (nonexistent)
    subcommand, producing a confusing "Missing argument" error. Peeling TABLE off
    manually here, before Click's own parser runs, sidesteps that limitation and
    lets ``db mappings TABLE --acceptProposed`` / ``db mappings TABLE set ...``
    both parse correctly.

    The group still declares a real ``table`` Argument — see ``db_mappings``
    below — so ``--help``/usage rendering knows about it (``get_params`` picks
    it up). ``make_parser`` below excludes it from the actual token-consuming
    parser, since real parsing is handled entirely by ``parse_args``'s manual
    peel; leaving it in would let it re-swallow whatever token comes right
    after TABLE (e.g. a subcommand name). It is a ``_PeeledArgument`` so that
    exclusion doesn't force it to be mis-declared as optional.
    """

    def make_parser(self, ctx: click.Context):
        original_params = self.params
        self.params = [p for p in original_params if p.name != "table"]
        try:
            return super().make_parser(ctx)
        finally:
            self.params = original_params

    def parse_args(self, ctx: click.Context, args: list[str]) -> list[str]:
        # Only "--help" is wired anywhere in this CLI (no help_option_names
        # override), so that's the only alias recognized here too.
        if args and args[0] != "--help":
            if args[0].startswith("-"):
                raise click.UsageError("Missing argument 'TABLE'.", ctx=ctx)
            table, *rest = args
            ctx.params["table"] = table
            args = rest
        elif not args:
            raise click.UsageError("Missing argument 'TABLE'.", ctx=ctx)
        return super().parse_args(ctx, args)


def _resolve_table_row(table_name: str, domain: "tuple[str, ...]") -> dict:
    """Resolve TABLE to a single registry row, mirroring db_schema's --domain handling."""
    domains = _parse_domains(domain) if domain else None
    matches = [t for t in structured_registry.list_tables(domains) if t["table_name"] == table_name]
    if not matches:
        raise click.ClickException(f"table '{table_name}' not found")
    if len(matches) > 1:
        doms = [t["domain"] for t in matches]
        raise click.ClickException(
            f"table '{table_name}' is ambiguous across domains {doms} — narrow with --domain"
        )
    return matches[0]


def _resolve_table_id(table_name: str, domain: "tuple[str, ...]") -> int:
    """Resolve TABLE to a single table_id, mirroring db_schema's --domain handling."""
    return _resolve_table_row(table_name, domain)["id"]


def _ctx_table_id(ctx: click.Context) -> int:
    """Resolve (once, then cache) the TABLE that ``_TableFirstGroup`` peeled off.

    Deliberately lazy. Click runs a group's callback *before* it parses the
    subcommand, so resolving in ``db_mappings`` itself meant a bad table
    short-circuited everything downstream — including
    ``db mappings SOME_TABLE clear --help``, which printed "table not found"
    rather than the help the user asked for. Resolving at point of use keeps
    ``--help`` free of database lookups while still failing loudly whenever a
    subcommand actually needs the table.

    ``ctx.obj`` is the parent group's dict (Click hands children the same
    object), so the cache is shared across the invocation.
    """
    if "table_id" not in ctx.obj:
        ctx.obj["table_id"] = _resolve_table_id(ctx.obj["table"], ctx.obj["domain"])
    return ctx.obj["table_id"]


@db.group("mappings", cls=_TableFirstGroup, invoke_without_command=True)
@click.argument("table", cls=_PeeledArgument, required=True, expose_value=False)
@click.option("--domain", "domain", multiple=True, help="Domain(s) to scope table resolution (repeatable; comma-splittable).")
@click.option("--acceptProposed", "accept_proposed", is_flag=True, help="Confirm all proposed mappings (bulk/non-interactive)")
@click.option("--compact", is_flag=True, help="Emit compact JSON")
@click.pass_context
def db_mappings(ctx, table, domain, accept_proposed, compact):
    """List proposed vs confirmed mappings for TABLE (default action)."""
    # Resolution is deferred to _ctx_table_id -- see its docstring.
    ctx.obj = {"table": table, "domain": domain}
    if ctx.invoked_subcommand is not None:
        return
    table_id = _ctx_table_id(ctx)
    if accept_proposed:
        for m in structured_registry.list_mappings(table_id):
            if not m["confirmed"]:
                structured_registry.set_mapping_confirmed(
                    table_id, m["column"], m["entity_class"], True
                )
    _echo_json({"table": table, "mappings": structured_registry.list_mappings(table_id)}, compact)


@db_mappings.command("set")
@click.option("--column", required=True)
@click.option("--entityClass", "entity_class", required=True)
@click.option("--confidence", type=float, default=1.0)
@click.option("--compact", is_flag=True, help="Emit compact JSON")
@click.pass_context
def db_mappings_set(ctx, column, entity_class, confidence, compact):
    """Upsert a confirmed column-to-entityClass mapping."""
    table_id = _ctx_table_id(ctx)
    structured_registry.upsert_mapping(table_id, column, entity_class, confidence, confirmed=True)
    _echo_json(
        {"table": ctx.obj["table"], "mappings": structured_registry.list_mappings(table_id)}, compact
    )


@db_mappings.command("confirm")
@click.option("--column", required=True)
@click.option("--entityClass", "entity_class", required=True)
@click.option("--compact", is_flag=True, help="Emit compact JSON")
@click.pass_context
def db_mappings_confirm(ctx, column, entity_class, compact):
    """Flip an existing proposed mapping to confirmed.

    Unlike ``clear`` (below), a no-match here is an error rather than a silent
    no-op: this command asserts a specific state transition on a specific row
    the caller believes already exists (typically from a proposal an operator
    is reviewing), so a typo'd --column/--entityClass should fail loudly rather
    than let the caller believe they confirmed something they didn't.
    """
    table_id = _ctx_table_id(ctx)
    rowcount = structured_registry.set_mapping_confirmed(table_id, column, entity_class, True)
    if rowcount == 0:
        raise click.ClickException(
            f"no mapping found for column '{column}' / entityClass '{entity_class}'"
        )
    _echo_json(
        {"table": ctx.obj["table"], "mappings": structured_registry.list_mappings(table_id)}, compact
    )


@db_mappings.command("clear")
@click.option("--column", default=None, help="Clear only this column's mappings (omit to clear all of the table's mappings)")
@click.option("--compact", is_flag=True, help="Emit compact JSON")
@click.pass_context
def db_mappings_clear(ctx, column, compact):
    """Remove mapping(s) for TABLE — one column, or all if --column omitted.

    Deliberately idempotent: a --column with no matching mappings is not an
    error (delete-if-present, like ``rm -f``), unlike ``confirm`` above, which
    asserts a specific row exists. Clearing is inherently "make sure this
    column has no mappings" rather than "act on this exact row", so a no-op is
    the correct, unsurprising result.
    """
    table_id = _ctx_table_id(ctx)
    structured_registry.clear_mappings(table_id, column)
    _echo_json(
        {"table": ctx.obj["table"], "mappings": structured_registry.list_mappings(table_id)}, compact
    )


@db.command("bridge")
@click.option("--domain", "domain", multiple=True, help="Domain(s) to scope (repeatable; comma-splittable). Omit for all.")
@click.option("--entityClass", "entity_class", default=None, help="Only tables whose class scope contains this class.")
@click.option("--compact", is_flag=True, help="Emit compact JSON")
def db_bridge(domain, entity_class, compact):
    """Read the structured↔graph bridge: class scope, bridge columns, grain.

    One call answers the three routing questions: does a structured store exist
    for this area, which tables are about the classes in the question, and which
    of their columns hold values worth searching the graph for.

    `entity_class` is the routing key — it is many-to-many with tables, which a
    single dotted `--domain` never could be. `bridge_columns` are the fusion
    step: feed those cell values to `query vector-text`/`entity-resolve`.
    """
    domains = _parse_domains(domain) if domain else None
    _echo_json(
        {
            "query_type": "structured",
            "command": "db bridge",
            "tables": structured_registry.routing_surface(domains, entity_class),
        },
        compact,
    )


@db.command("propose")
@click.argument("table")
@click.option("--domain", "domain", multiple=True, help="Domain(s) to scope table resolution (repeatable; comma-splittable).")
@click.option("--model", default=None, help="Override the LLM model used for the grain/bridge proposal.")
@click.option("--skipSemantics", "skip_semantics", is_flag=True, help="Re-propose mappings only — no LLM call.")
@click.option("--compact", is_flag=True, help="Emit compact JSON")
def db_propose(table, domain, model, skip_semantics, compact):
    """Re-run semantic proposals for TABLE: mappings, grain, and bridge columns.

    The ingest pipeline proposes mappings on every write and grain/bridge_role
    only on first registration, so this is how a table gets re-examined without
    re-ingesting the source file — the same role ``db catalogue`` plays for the
    Neo4j projection. Confirmed values are never overwritten by a re-proposal.
    """
    from artmind.structured.mappings import propose_mappings

    row = _resolve_table_row(table, domain)
    table_id = row["id"]
    result = {"table": table, "domain": row["domain"]}

    try:
        result["mappings"] = propose_mappings(table_id, [row["domain"]])
    except Exception as exc:
        raise click.ClickException(f"mapping proposal failed: {exc}") from exc

    if not skip_semantics:
        from artmind.structured.semantics import propose_semantics

        try:
            result["semantics"] = propose_semantics(table_id, model=model)
        except Exception as exc:
            raise click.ClickException(f"semantics proposal failed: {exc}") from exc

    _echo_json(result, compact)


@db.command("grain")
@click.argument("table")
@click.option("--domain", "domain", multiple=True, help="Domain(s) to scope table resolution (repeatable; comma-splittable).")
@click.option("--set", "grain", default=None, type=click.Choice(structured_registry.GRAINS), help="Confirm TABLE's grain. Omit to just show it.")
@click.option("--compact", is_flag=True, help="Emit compact JSON")
def db_grain(table, domain, grain, compact):
    """Show or confirm what TABLE's rows denote — instance, lookup, or normative.

    Only `normative` changes behaviour: it means a document also asserts this
    content, so the graph wins on disagreement and the table is quarantined from
    answer synthesis. Confirming it requires refresh_mode=temporal.
    """
    row = _resolve_table_row(table, domain)
    if grain is not None:
        try:
            structured_registry.set_grain(row["id"], grain, confirmed=True)
        except ValueError as exc:
            raise click.ClickException(str(exc)) from exc
        row = structured_registry.get_table_by_id(row["id"])
    _echo_json(
        {
            "table": row["table_name"],
            "domain": row["domain"],
            "grain": row["grain"],
            "grainConfirmed": bool(row["grain_confirmed"]),
            "refreshMode": row["refresh_mode"],
            "bridgeColumns": structured_registry.list_column_roles(row["id"]),
        },
        compact,
    )


@db.command("catalogue")
@click.option("--domain", "domain", required=True, help="Domain to rebuild the catalogue subgraph for (rolls up sub-domains)")
@click.option("--compact", is_flag=True, help="Emit compact JSON")
def db_catalogue(domain, compact):
    """Rebuild the Neo4j catalogue subgraph (Table/TableColumn/EntityClass) for a domain from the registry.

    Manual/on-demand — the ingest pipeline already best-effort-projects on
    every write, but confirming a mapping later (`db mappings ... set`/
    `confirm`) doesn't re-ingest, so this is how that later confirmation gets
    reflected in the graph without a re-ingest. Unlike the ingest hook, a
    failure here is surfaced (not swallowed) — the operator explicitly asked
    for this projection to happen.
    """
    from artmind.structured.catalogue import project_catalogue

    try:
        result = project_catalogue(domain)
    except Exception as exc:
        raise click.ClickException(f"catalogue projection failed: {exc}") from exc
    _echo_json({"domain": domain, **result}, compact)


@db.command("refresh")
@click.argument("table")
@click.option("--domain", "domain", multiple=True, help="Domain(s) to scope table resolution (repeatable; comma-splittable) — needed only if TABLE is ambiguous across domains.")
@click.option("--compact", is_flag=True, help="Emit compact JSON")
def db_refresh(table, domain, compact):
    """Re-ingest TABLE from its recorded source_file.

    ``replace``-mode tables are overwritten wholesale (same as a fresh
    ingest); ``temporal``-mode tables are diffed against their existing SCD-2
    history and the parquet is rewritten with the merged full history — prior
    versions of changed/removed rows are preserved, not discarded.
    """
    resolved_domain = _resolve_table_row(table, domain)["domain"]
    try:
        result = refresh_table(table, resolved_domain)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    except duckdb.Error as exc:
        # Defensive backstop: pipeline.py validates the common schema-drift
        # case (column set mismatch) up front and raises ValueError for it
        # (handled above with a clear message); this catches any other
        # DuckDB-level surprise so a temporal refresh never leaks a raw
        # traceback to the CLI.
        raise click.ClickException(f"refresh failed: {exc}") from exc
    _echo_json(result, compact)


@db.command("connect")
@click.argument("dsn")
def db_connect(dsn):
    """Reserve the external-adapter surface (stubbed in v1 — DuckDB only)."""
    raise click.ClickException("external adapters not available in v1 — DuckDB only")


@db.command("backup")
@click.option("--compact", is_flag=True, help="Emit compact JSON")
def db_backup(compact):
    """Snapshot the structured store (parquet files + registry rows) to a tar.gz."""
    path = export_structured()
    _echo_json({"path": str(path), "name": path.name}, compact)


@db.command("restore")
@click.argument("path", required=False, type=click.Path(exists=True))
@click.option("--confirm", is_flag=True, help="Required — restoring wipes the current structured store")
@click.option("--compact", is_flag=True, help="Emit compact JSON")
def db_restore(path, confirm, compact):
    """Wipe and restore the structured store from a snapshot (latest if PATH omitted)."""
    if not confirm:
        raise click.ClickException("pass --confirm — restoring wipes the current structured store")
    try:
        summary = import_structured(Path(path) if path else None)
    except FileNotFoundError as exc:
        raise click.ClickException(str(exc)) from exc
    _echo_json(summary, compact)


@cli.group()
def query():
    """Query the knowledge graph and vector index, plus the structured store (text2sql, resolve-key)."""
    pass


@query.group()
def graph():
    """Execute graph queries (metadata, entity listing, pattern1–pattern10, timeline, conflicts, text2cypher)."""
    pass


def _run_graph_pattern(
    pattern: str, domains: list[str], compact: bool, question: str | None, as_of: str | None = None, **kwargs
) -> None:
    try:
        result = graph_query.execute_pattern(
            domains=domains, pattern=pattern, question=question, as_of=as_of, **kwargs
        )
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    _echo_json(result, compact)


@graph.command("metadata")
@click.option("--domain", "domain", required=True, multiple=True, help="Domain to query (repeatable; comma-splittable)")
@click.option("--asOf", "as_of", default=None, help="Valid-time filter: ISO date or 'today'; nodes without valid-time always shown")
@click.option("--compact", is_flag=True, help="Emit compact JSON")
def graph_metadata_cmd(domain: tuple, as_of: str | None, compact: bool) -> None:
    """Return graph schema metadata (labels, properties, relationship types)."""
    domains = _parse_domains(domain)
    try:
        result = graph_query.graph_metadata(domains, as_of=as_of)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    _echo_json(result, compact)


@graph.command("structural-metadata")
@click.option("--domain", "domain", required=True, multiple=True, help="Domain to query (repeatable; comma-splittable)")
@click.option("--compact", is_flag=True, help="Emit compact JSON")
def graph_structural_metadata_cmd(domain: tuple, compact: bool) -> None:
    """Return focused structural metadata (Document, DocChunk, UserChat, Entity counts and relationships)."""
    domains = _parse_domains(domain)
    _echo_json(graph_query.structural_metadata(domains), compact)


@graph.command("entity-listing")
@click.option("--domain", "domain", required=True, multiple=True, help="Domain to query (repeatable; comma-splittable)")
@click.option("--nameFilter", "name_filter", default=None, help="Fuzzy match entity names (case-insensitive substring)")
@click.option("--countAll", "count_all", is_flag=True, help="Include total unfiltered entity count in output")
@click.option("--asOf", "as_of", default=None, help="Valid-time filter: ISO date or 'today'; nodes without valid-time always shown")
@click.option("--compact", is_flag=True, help="Emit compact JSON")
def graph_entity_listing_cmd(domain: tuple, name_filter: str | None, count_all: bool, as_of: str | None, compact: bool) -> None:
    """Return entity names grouped by label/type."""
    domains = _parse_domains(domain)
    try:
        result = graph_query.entity_listing(domains, name_filter=name_filter, count_all=count_all, as_of=as_of)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    _echo_json(result, compact)


@graph.command("pattern1")
@click.option("--domain", "domain", required=True, multiple=True, help="Domain to query (repeatable; comma-splittable)")
@click.option("--entityClass", "entity_class", required=True, help="Entity class label (e.g. CHARACTER, LOCATION)")
@click.option("--limit", type=int, default=200, show_default=True, help="Max results")
@click.option("--asOf", "as_of", default=None, help="Valid-time filter: ISO date or 'today'; nodes without valid-time always shown")
@click.option("--compact", is_flag=True, help="Emit compact JSON")
@click.argument("question", required=False)
def graph_pattern1(
    domain: tuple, entity_class: str, limit: int, as_of: str | None, compact: bool, question: str | None
) -> None:
    """List entities of a class."""
    domains = _parse_domains(domain)
    _run_graph_pattern(
        "pattern1", domains, compact, question, as_of=as_of, entityClass=entity_class, limit=limit
    )


@graph.command("pattern2")
@click.option("--domain", "domain", required=True, multiple=True, help="Domain to query (repeatable; comma-splittable)")
@click.option("--entityNameList", "entity_name_list", multiple=True, help="Entity name (repeatable, substring match)")
@click.option("--entityIdList", "entity_id_list", multiple=True, help="Exact entity id (repeatable, overrides name list)")
@click.option("--asOf", "as_of", default=None, help="Valid-time filter: ISO date or 'today'; nodes without valid-time always shown")
@click.option("--compact", is_flag=True, help="Emit compact JSON")
@click.argument("question", required=False)
def graph_pattern2(
    domain: tuple, entity_name_list: tuple, entity_id_list: tuple, as_of: str | None, compact: bool, question: str | None
) -> None:
    """Info on one or more named entities."""
    domains = _parse_domains(domain)
    _run_graph_pattern(
        "pattern2", domains, compact, question, as_of=as_of,
        entityNameList=entity_name_list, entityIdList=entity_id_list,
    )


@graph.command("pattern3")
@click.option("--domain", "domain", required=True, multiple=True, help="Domain to query (repeatable; comma-splittable)")
@click.option("--entityNameList", "entity_name_list", multiple=True, help="Entity name (repeatable, substring match)")
@click.option("--entityIdList", "entity_id_list", multiple=True, help="Exact entity id (repeatable, overrides name list)")
@click.option("--asOf", "as_of", default=None, help="Valid-time filter: ISO date or 'today'; nodes without valid-time always shown")
@click.option("--compact", is_flag=True, help="Emit compact JSON")
@click.argument("question", required=False)
def graph_pattern3(
    domain: tuple, entity_name_list: tuple, entity_id_list: tuple, as_of: str | None, compact: bool, question: str | None
) -> None:
    """Entity + lightweight relationship summary."""
    domains = _parse_domains(domain)
    _run_graph_pattern(
        "pattern3", domains, compact, question, as_of=as_of,
        entityNameList=entity_name_list, entityIdList=entity_id_list,
    )


@graph.command("pattern4")
@click.option("--domain", "domain", required=True, multiple=True, help="Domain to query (repeatable; comma-splittable)")
@click.option("--entityClass", "entity_class", required=True, help="Entity class label")
@click.option("--entityName", "entity_name", default=None, help="Entity name (substring match)")
@click.option("--entityId", "entity_id", default=None, help="Exact entity id (overrides --entityName)")
@click.option("--asOf", "as_of", default=None, help="Valid-time filter: ISO date or 'today'; nodes without valid-time always shown")
@click.option("--compact", is_flag=True, help="Emit compact JSON")
@click.argument("question", required=False)
def graph_pattern4(
    domain: tuple, entity_class: str, entity_name: str | None, entity_id: str | None,
    as_of: str | None, compact: bool, question: str | None
) -> None:
    """Entity + full neighborhood."""
    domains = _parse_domains(domain)
    _run_graph_pattern(
        "pattern4", domains, compact, question, as_of=as_of,
        entityClass=entity_class, entityName=entity_name, entityId=entity_id,
    )


@graph.command("pattern5")
@click.option("--domain", "domain", required=True, multiple=True, help="Domain to query (repeatable; comma-splittable)")
@click.option("--entityClass1", "entity_class1", required=True, help="Class of first entity")
@click.option("--entityClass2", "entity_class2", required=True, help="Class of second entity")
@click.option("--entityName1", "entity_name1", default=None, help="Name of first entity")
@click.option("--entityName2", "entity_name2", default=None, help="Name of second entity")
@click.option("--entityId1", "entity_id1", default=None, help="Exact id of first entity (overrides --entityName1)")
@click.option("--entityId2", "entity_id2", default=None, help="Exact id of second entity (overrides --entityName2)")
@click.option(
    "--mode",
    type=click.Choice(["shortest", "all"]),
    default="shortest",
    show_default=True,
    help="Path mode",
)
@click.option("--asOf", "as_of", default=None, help="Accepted but IGNORED by pattern5 (paths cannot be currency-scoped); output carries asOf_ignored")
@click.option("--compact", is_flag=True, help="Emit compact JSON")
@click.argument("question", required=False)
def graph_pattern5(
    domain: tuple,
    entity_class1: str,
    entity_class2: str,
    entity_name1: str | None,
    entity_name2: str | None,
    entity_id1: str | None,
    entity_id2: str | None,
    mode: str,
    as_of: str | None,
    compact: bool,
    question: str | None,
) -> None:
    """Paths between two entities (shortest or all within depth 5)."""
    domains = _parse_domains(domain)
    _run_graph_pattern(
        "pattern5", domains, compact, question, as_of=as_of,
        entityClass1=entity_class1,
        entityClass2=entity_class2,
        entityName1=entity_name1,
        entityName2=entity_name2,
        entityId1=entity_id1,
        entityId2=entity_id2,
        mode=mode,
    )


@graph.command("pattern6")
@click.option("--domain", "domain", required=True, multiple=True, help="Domain to query (repeatable; comma-splittable)")
@click.option("--entityName1", "entity_name1", default=None, help="Name of first entity")
@click.option("--entityName2", "entity_name2", default=None, help="Name of second entity")
@click.option("--entityId1", "entity_id1", default=None, help="Exact id of first entity (overrides --entityName1)")
@click.option("--entityId2", "entity_id2", default=None, help="Exact id of second entity (overrides --entityName2)")
@click.option("--asOf", "as_of", default=None, help="Valid-time filter: ISO date or 'today'; nodes without valid-time always shown")
@click.option("--compact", is_flag=True, help="Emit compact JSON")
@click.argument("question", required=False)
def graph_pattern6(
    domain: tuple,
    entity_name1: str | None,
    entity_name2: str | None,
    entity_id1: str | None,
    entity_id2: str | None,
    as_of: str | None,
    compact: bool,
    question: str | None,
) -> None:
    """Direct relationships between two entities."""
    domains = _parse_domains(domain)
    _run_graph_pattern(
        "pattern6", domains, compact, question, as_of=as_of,
        entityName1=entity_name1, entityName2=entity_name2,
        entityId1=entity_id1, entityId2=entity_id2,
    )


@graph.command("pattern7")
@click.option("--domain", "domain", required=True, multiple=True, help="Domain to query (repeatable; comma-splittable)")
@click.option("--searchTerm", "search_term", required=True, help="Substring match on name or description")
@click.option("--limit", type=int, default=10, show_default=True, help="Max results")
@click.option("--asOf", "as_of", default=None, help="Valid-time filter: ISO date or 'today'; nodes without valid-time always shown")
@click.option("--compact", is_flag=True, help="Emit compact JSON")
@click.argument("question", required=False)
def graph_pattern7(
    domain: tuple, search_term: str, limit: int, as_of: str | None, compact: bool, question: str | None
) -> None:
    """Search entities by name or description fragment."""
    domains = _parse_domains(domain)
    _run_graph_pattern("pattern7", domains, compact, question, as_of=as_of, searchTerm=search_term, limit=limit)


@graph.command("pattern8")
@click.option("--domain", "domain", required=True, multiple=True, help="Domain to query (repeatable; comma-splittable)")
@click.option("--entityClass", "entity_class", required=True, help="Class of entities to return")
@click.option("--entityName", "entity_name", default=None, help="Name of the connected entity")
@click.option("--entityId", "entity_id", default=None, help="Exact id of the connected entity (overrides --entityName)")
@click.option("--asOf", "as_of", default=None, help="Valid-time filter: ISO date or 'today'; nodes without valid-time always shown")
@click.option("--compact", is_flag=True, help="Emit compact JSON")
@click.argument("question", required=False)
def graph_pattern8(
    domain: tuple, entity_class: str, entity_name: str | None, entity_id: str | None,
    as_of: str | None, compact: bool, question: str | None
) -> None:
    """Entities of class X connected to entity Y."""
    domains = _parse_domains(domain)
    _run_graph_pattern(
        "pattern8", domains, compact, question, as_of=as_of,
        entityClass=entity_class, entityName=entity_name, entityId=entity_id,
    )


@graph.command("pattern9")
@click.option("--domain", "domain", required=True, multiple=True, help="Domain to query (repeatable; comma-splittable)")
@click.option("--entityClass", "entity_class", required=True, help="Entity class label")
@click.option("--topN", "top_n", type=int, default=5, show_default=True, help="Number of top entities")
@click.option(
    "--degreeMode",
    "degree_mode",
    type=click.Choice(["relations", "mentions", "all"]),
    default="relations",
    show_default=True,
    help="Degree to rank by: entity-entity relationships, source mentions, or all edges",
)
@click.option("--asOf", "as_of", default=None, help="Valid-time filter: ISO date or 'today'; nodes without valid-time always shown")
@click.option("--compact", is_flag=True, help="Emit compact JSON")
@click.argument("question", required=False)
def graph_pattern9(
    domain: tuple, entity_class: str, top_n: int, degree_mode: str, as_of: str | None, compact: bool, question: str | None
) -> None:
    """Top-N entities of a class by connection count."""
    domains = _parse_domains(domain)
    _run_graph_pattern(
        "pattern9", domains, compact, question, as_of=as_of,
        entityClass=entity_class, topN=top_n, degreeMode=degree_mode,
    )


@graph.command("pattern10")
@click.option("--domain", "domain", required=True, multiple=True, help="Domain to query (repeatable; comma-splittable)")
@click.option("--documentName", "document_name", required=True, help="Document name (substring match)")
@click.option("--asOf", "as_of", default=None, help="Accepted but IGNORED by pattern10 (chunk currency isn't modeled); output carries asOf_ignored")
@click.option("--compact", is_flag=True, help="Emit compact JSON")
@click.argument("question", required=False)
def graph_pattern10(
    domain: tuple, document_name: str, as_of: str | None, compact: bool, question: str | None
) -> None:
    """Retrieve all text chunks for a named document."""
    domains = _parse_domains(domain)
    _run_graph_pattern("pattern10", domains, compact, question, as_of=as_of, documentName=document_name)


@graph.command("text2cypher")
@click.option("--domain", "domain", required=True, multiple=True, help="Domain to query (repeatable; comma-splittable)")
@click.option("--compact", is_flag=True, help="Emit compact JSON")
@click.option("--dry-run", "dry_run", is_flag=True, help="Show generated Cypher without executing it")
@click.argument("question")
def graph_text2cypher(domain: tuple, compact: bool, dry_run: bool, question: str) -> None:
    """Generate and execute a Cypher query from a natural-language question (LLM-powered)."""
    domains = _parse_domains(domain)
    try:
        result = text2cypher.execute_text2cypher(
            question=question, domains=domains, dry_run=dry_run
        )
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    _echo_json(result, compact)


@graph.command("conflicts")
@click.option("--domain", "domain", required=True, multiple=True, help="Domain(s) (repeatable)")
@click.option("--entityId", "entity_id", multiple=True, help="Filter to conflicts touching this entity id (repeatable)")
@click.option("--entityName", "entity_name", default=None, help="Filter to conflicts touching an entity whose name contains this")
@click.option("--status", type=click.Choice(["open", "resolved", "dismissed", "all"]), default="open", show_default=True)
@click.option("--compact", is_flag=True, help="Emit compact JSON")
def graph_conflicts(domain: tuple, entity_id: tuple, entity_name: str | None, status: str, compact: bool) -> None:
    """List materialized Conflict nodes scoped to the given domains."""
    domains = _parse_domains(domain)
    _echo_json(
        graph_query.list_conflicts(domains, entity_ids=list(entity_id), entity_name=entity_name, status=status),
        compact,
    )


@graph.command("timeline")
@click.option("--domain", "domain", required=True, multiple=True, help="Domain(s)")
@click.option("--entityId", "entity_id", required=True, help="Entity id whose timeline to render")
@click.option("--compact", is_flag=True, help="Emit compact JSON")
def graph_timeline(domain: tuple, entity_id: str, compact: bool) -> None:
    """Events/state-changes/supersessions for an entity, ordered by time."""
    domains = _parse_domains(domain)
    _echo_json(graph_query.list_timeline(domains, entity_id), compact)


@graph.command("entity-versions")
@click.option("--domain", "domain", required=True, multiple=True, help="Domain(s)")
@click.option("--entityId", "entity_id", required=True, help="Entity id whose prior states to list")
@click.option("--asOf", "as_of", default=None, help="Return the state in force on this ISO date (omit for the full chain)")
@click.option("--compact", is_flag=True, help="Emit compact JSON")
def graph_entity_versions(domain: tuple, entity_id: str, as_of: str | None, compact: bool) -> None:
    """Prior states of an entity from the history zone (superseded property values)."""
    domains = _parse_domains(domain)
    try:
        result = graph_query.entity_versions(domains, entity_id, as_of=as_of)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    _echo_json(result, compact)


@query.command("domains-overview")
@click.option("--compact", is_flag=True, help="Emit compact JSON")
def query_domains_overview(compact: bool) -> None:
    """Per-domain routing summary: doc names/counts, entity counts, top classes."""
    _echo_json(graph_query.domains_overview(), compact)


@query.command("vector-text")
@click.option("--domain", "domain", required=True, multiple=True, help="Domain to query (repeatable; comma-splittable)")
@click.option("--topK", "top_k", type=int, default=5, show_default=True)
@click.option("--asOf", "as_of", default=None, help="Valid-time filter: ISO date or 'today'; nodes without valid-time always shown")
@click.option("--compact", is_flag=True, help="Emit compact JSON")
@click.argument("question")
def vector_text(domain: tuple, top_k: int, as_of: str | None, compact: bool, question: str) -> None:
    """Search source text using vector embeddings and keyword matching combined via RRF."""
    domains = _parse_domains(domain)
    try:
        result = vector_query.vector_text_search(domains, question, top_k, as_of=as_of)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    _echo_json(result, compact)


@query.command("entity-resolve")
@click.option("--domain", "domain", required=True, multiple=True, help="Domain to query (repeatable; comma-splittable)")
@click.option("--topK", "top_k", type=int, default=5, show_default=True)
@click.option("--asOf", "as_of", default=None, help="Valid-time filter: ISO date or 'today'; nodes without valid-time always shown")
@click.option("--compact", is_flag=True, help="Emit compact JSON")
@click.argument("reference")
def query_entity_resolve(domain: tuple, top_k: int, as_of: str | None, compact: bool, reference: str) -> None:
    """Resolve a name fragment or description to canonical graph entities (fulltext + vector via RRF)."""
    domains = _parse_domains(domain)
    try:
        result = vector_query.entity_resolve(domains, reference, top_k, as_of=as_of)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    _echo_json(result, compact)


@query.command("chunks")
@click.option("--domain", "domain", required=True, multiple=True, help="Domain to query (repeatable; comma-splittable)")
@click.option("--idList", "id_list", multiple=True, required=True, help="Exact chunk id (repeatable)")
@click.option("--expand", type=int, default=0, show_default=True, help="Also return up to N adjacent chunks per hit (same document)")
@click.option("--asOf", "as_of", default=None, help="Valid-time filter: ISO date or 'today'; nodes without valid-time always shown")
@click.option("--compact", is_flag=True, help="Emit compact JSON")
def query_chunks(domain: tuple, id_list: tuple, expand: int, as_of: str | None, compact: bool) -> None:
    """Fetch chunk text by exact id (the doc_sources / evidence ids other commands return)."""
    domains = _parse_domains(domain)
    try:
        result = graph_query.chunks_by_id(domains, list(id_list), expand=expand, as_of=as_of)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    _echo_json(result, compact)


@query.command("entity-context")
@click.option("--domain", "domain", required=True, multiple=True, help="Domain to query (repeatable; comma-splittable)")
@click.option("--entityId", "entity_id", required=True, help="Exact entity id (from entity-resolve)")
@click.option("--includeChunks", "include_chunks", type=int, default=5, show_default=True, help="Source chunks returned with full text (rest as ids)")
@click.option("--asOf", "as_of", default=None, help="Valid-time filter: ISO date or 'today'; applies to the entity and its source chunks")
@click.option("--compact", is_flag=True, help="Emit compact JSON")
def query_entity_context(domain: tuple, entity_id: str, include_chunks: int, as_of: str | None, compact: bool) -> None:
    """Entity properties + one-hop relationships + source chunk text in one call."""
    domains = _parse_domains(domain)
    try:
        result = graph_query.entity_context(domains, entity_id, include_chunks=include_chunks, as_of=as_of)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    _echo_json(result, compact)


@query.command("text2sql")
@click.option("--domain", "domain", required=True, multiple=True, help="Domain(s) to scope (repeatable; comma-splittable)")
@click.option("--asOf", "as_of", default=None, help="Valid-time filter: ISO date or 'today'; rows without temporal history always shown")
@click.option("--dry-run", "dry_run", is_flag=True, help="Generate SQL without executing it")
@click.option("--compact", is_flag=True, help="Emit compact JSON")
@click.argument("question")
def query_text2sql(domain: tuple, as_of: str | None, dry_run: bool, compact: bool, question: str) -> None:
    """Natural language to read-only DuckDB SQL against the structured store, then execute it."""
    domains = _parse_domains(domain)
    try:
        result = text2sql.execute_text2sql(question, domains, dry_run=dry_run, as_of=as_of)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    _echo_json(result, compact)


@query.command("resolve-key")
@click.option("--domain", "domain", required=True, multiple=True, help="Domain to query (repeatable; comma-splittable)")
@click.option("--column", "column", default=None, help="Scope matching to this column's profiled values (omit to resolve against the graph only)")
@click.option("--table", "table", default=None, help="Table containing --column, if ambiguous across tables (optional)")
@click.option("--topK", "top_k", type=int, default=5, show_default=True)
@click.option("--compact", is_flag=True, help="Emit compact JSON")
@click.argument("phrase")
def query_resolve_key(domain: tuple, column: str | None, table: str | None, top_k: int, compact: bool, phrase: str) -> None:
    """Resolve a free-text value to a canonical column value and/or graph entity."""
    domains = _parse_domains(domain)
    try:
        result = resolve_key.resolve_key(phrase, domains, column=column, table=table, top_k=top_k)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    _echo_json(result, compact)


# ── artmind docs ───────────────────────────────────────────────────────────────


@cli.group()
def docs():
    """Manage ingested documents."""
    pass


@docs.command("clean")
@click.option("--domain", required=True, help="Domain containing the document")
@click.argument("document_name")
def docs_clean(domain: str, document_name: str):
    """Delete a document from local storage, registry, and Neo4j."""
    _setup_logger()
    result = clean_document(domain, document_name)
    click.echo(f"Cleaned '{result['document_name']}' in domain '{result['domain']}'")
    click.echo(f"  registry rows: {result['registry_rows']}")
    click.echo(f"  originals: {result['originals']}")
    click.echo(f"  markdowns: {result['markdowns']}")
    click.echo(f"  markdown artifacts: {result['markdown_artifacts']}")
    click.echo(f"  kg dirs: {result['kg_dirs']}")
    click.echo(f"  chunk status rows: {result['chunk_status_rows']}")
    click.echo(f"  neo4j documents: {result['neo4j_documents']}")
    click.echo(f"  neo4j chunks: {result['neo4j_chunks']}")
    click.echo(f"  neo4j orphan entities: {result['neo4j_orphan_entities']}")
    if result["neo4j_error"]:
        raise click.ClickException(f"Neo4j cleanup failed: {result['neo4j_error']}")


# ── artmind update ─────────────────────────────────────────────────────────────


@cli.group()
def update():
    """Add and update knowledge graph facts from natural language.

    Subcommands: draft, confirm, supersede (retire a node in favor of a newer
    one — the node-level counterpart to `ingest supersede`'s document-level
    supersession), history, export.
    """
    pass


@update.command("draft")
@click.option("--domain", required=True, help="Domain name.")
@click.option("--text", required=True, help="Raw user input text.")
@click.option("--session", default=None, help="Resume an existing session UUID.")
def update_draft(domain: str, text: str, session: str | None):
    """Extract facts and find graph candidates. Returns JSON."""
    _setup_logger()
    env = load_env()
    user_id = env.get("ARTMIND_USER", "unknown")
    try:
        result = update_backend.draft_update(
            domain=domain, text=text, session_id=session, user_id=user_id
        )
        _echo_json(result)
    except Exception as e:
        raise click.ClickException(str(e))


@update.command("confirm")
@click.option("--session", required=True, help="Session UUID from draft step.")
@click.option("--resolutions", required=True, help="JSON array of resolution objects.")
def update_confirm(session: str, resolutions: str):
    """Write confirmed facts to Neo4j. Returns JSON."""
    _setup_logger()
    env = load_env()
    user_id = env.get("ARTMIND_USER", "unknown")
    try:
        parsed = json.loads(resolutions)
        result = update_backend.confirm_update(
            session_id=session, resolutions=parsed, user_id=user_id
        )
        _echo_json(result)
    except ValueError as e:
        raise click.ClickException(str(e))
    except Exception as e:
        raise click.ClickException(str(e))


@update.command("supersede")
@click.option("--newer", "newer_ref", required=True, help="id of the entity that replaces the older one.")
@click.option("--older", "older_ref", required=True, help="id of the entity being retired.")
@click.option("--effective", default=None, help="ISO date the supersession takes effect (default: today).")
@click.option("--reason", default=None, help="Optional free-text reason, for audit purposes.")
@click.option("--compact", is_flag=True, help="Emit compact JSON")
def update_supersede(
    newer_ref: str, older_ref: str, effective: str | None, reason: str | None, compact: bool
) -> None:
    """Mark one entity node as superseding another (node-level, not document-level).

    Use when a new fact replaces an existing node rather than a property on it
    — e.g. a role holder or an address changing. Sets a SUPERSEDES edge and
    stamps valid_to/superseded_by/status on the older node; nothing is deleted.
    --newer and --older each accept either identifier an agent is likely to
    already have in hand: the `node_id` (Neo4j elementId) returned by
    `update draft`'s candidates, or the `id` property returned by
    `entity-context`/`query graph pattern2`/etc.
    """
    _setup_logger()
    from artmind.graph_query import neo4j_session
    from artmind.temporal import apply_node_supersession

    with neo4j_session() as session:
        rec = session.run(
            "MATCH (e:Entity) WHERE elementId(e) = $ref OR e.id = $ref RETURN e.id AS id",
            ref=newer_ref,
        ).single()
    if not rec or not rec["id"]:
        raise click.ClickException(f"Could not resolve newer entity id={newer_ref!r}")

    try:
        result = apply_node_supersession(
            newer_id=rec["id"],
            older_id=older_ref,
            effective=effective or date.today().isoformat(),
            detected_by="manual",
            reason=reason,
        )
    except ValueError as e:
        raise click.ClickException(str(e))
    _echo_json(result, compact)


@update.command("history")
@click.option("--domain", default=None, help="Filter by domain.")
@click.option("--user", default=None, help="Filter by created_by.")
@click.option("--limit", default=20, show_default=True, help="Maximum rows to return.")
def update_history(domain: str | None, user: str | None, limit: int):
    """List recent update sessions."""
    sessions = update_backend._list_update_sessions(domain=domain, user=user, limit=limit)
    if not sessions:
        click.echo("No update sessions found.")
        return
    header = f"{'SESSION':<12} {'DOMAIN':<12} {'BY':<24} {'AT':<22} {'INPUTS':>6}  EXCERPT"
    click.echo(header)
    click.echo("-" * len(header))
    for s in sessions:
        click.echo(
            f"{s['session_id'][:12]:<12} {s['domain']:<12} {s['created_by']:<24}"
            f" {s['created_at'][:19]:<22} {s['input_count']:>6}  {s['excerpt']}"
        )


@update.command("export")
@click.option("--domain", default=None, help="Filter by domain.")
@click.option(
    "--format", "fmt",
    type=click.Choice(["sequential", "by-entity"], case_sensitive=False),
    default="sequential",
    show_default=True,
)
@click.option(
    "--output",
    type=click.Path(file_okay=False, writable=True),
    default="data/chats",
    show_default=True,
)
def update_export(domain: str | None, fmt: str, output: str):
    """Export UserChat nodes to markdown files."""
    output_dir = Path(output)
    written = update_backend.export_chats(domain=domain, format=fmt, output_dir=output_dir)
    if not written:
        click.echo("No chats to export.")
        return
    for path in written:
        click.echo(str(path.name))
    click.echo(f"\nExported {len(written)} file(s) to {output_dir}")


# ── artmind session ────────────────────────────────────────────────────────────


@cli.group()
def session():
    """Save and restore the Neo4j graph between sessions."""
    pass


@session.command("close")
def session_close():
    """Export the full Neo4j graph to a compressed snapshot (end of session)."""
    _setup_logger()
    try:
        snapshot_path = export_graph()
        size_mb = snapshot_path.stat().st_size / (1024 * 1024)
        click.echo(f"Snapshot saved: {snapshot_path}")
        click.echo(f"  Size: {size_mb:.2f} MB")
    except Exception as e:
        raise click.ClickException(str(e))


@session.command("initiate")
@click.option("--snapshot", "snapshot_file", default=None, type=click.Path(exists=True),
              help="Path to a specific snapshot .tar.gz (default: latest in data/graph_snapshot/)")
@click.option("--yes", is_flag=True, help="Skip confirmation prompt")
def session_initiate(snapshot_file: str | None, yes: bool):
    """Wipe Neo4j and restore from a snapshot (start of session)."""
    _setup_logger()
    if not yes:
        env = load_env()
        db_name = env.get("ARTMIND_KG_NEO4J_DATABASE", "neo4j")
        if not click.confirm(
            f"This will delete all data in Neo4j database '{db_name}'. Continue?"
        ):
            raise click.Abort()

    snapshot_path = Path(snapshot_file) if snapshot_file else None
    try:
        summary = import_graph(snapshot_path)
        click.echo(f"Restored from: {summary['snapshot']}")
        node_counts = summary.get("node_counts", {})
        parts = [f"{label}: {count}" for label, count in node_counts.items()]
        click.echo(f"  Nodes: {' | '.join(parts)}")
        click.echo(f"  Relationships: {summary['relationship_count']}")
        click.echo(f"  Elapsed: {summary['elapsed_seconds']}s")
    except FileNotFoundError as e:
        raise click.ClickException(str(e))
    except Exception as e:
        raise click.ClickException(str(e))


# ── artmind snapshot ───────────────────────────────────────────────────────────


@cli.group()
def snapshot():
    """Create and restore unified snapshots (graph, registry, structured, KG JSONs)."""
    pass


@snapshot.command("create")
@click.option(
    "--only",
    default=None,
    help="Comma-separated components to include (graph,registry,structured,kg_staging). "
         "Default: all",
)
@click.option("--compact", is_flag=True, help="Emit compact JSON")
def snapshot_create(only: str | None, compact: bool):
    """Create a unified snapshot of all system state.

    Bundles graph, registry, structured data, and KG staging JSONs into one zip file
    with a manifest for selective restore.
    """
    _setup_logger()
    try:
        components = None
        if only:
            components = set(only.replace(" ", "").split(","))
            valid = {"graph", "registry", "structured", "kg_staging"}
            invalid = components - valid
            if invalid:
                raise click.ClickException(
                    f"Invalid components: {invalid}. Choose from: {', '.join(sorted(valid))}"
                )

        snapshot_path = create_snapshot(include=components)
        size_mb = snapshot_path.stat().st_size / (1024 * 1024)
        summary = {
            "snapshot": snapshot_path.name,
            "path": str(snapshot_path),
            "size_mb": round(size_mb, 2),
        }
        if components:
            summary["components"] = sorted(components)
        click.echo(f"Snapshot created: {snapshot_path.name}")
        click.echo(f"  Size: {size_mb:.2f} MB")
        if components:
            click.echo(f"  Components: {', '.join(sorted(components))}")
        _echo_json(summary, compact)
    except Exception as e:
        raise click.ClickException(str(e))


@snapshot.command("restore")
@click.argument("snapshot_path", type=click.Path(exists=True))
@click.option(
    "--only",
    default=None,
    help="Comma-separated components to restore (graph,registry,structured,kg_staging). "
         "Default: all available",
)
@click.option("--yes", is_flag=True, help="Skip confirmation prompt")
@click.option("--compact", is_flag=True, help="Emit compact JSON")
def snapshot_restore(snapshot_path: str, only: str | None, yes: bool, compact: bool):
    """Restore from a unified snapshot.

    Wipes and restores selected components (or all if --only not specified).
    Warns if restoring a subset could cause state divergence.
    """
    _setup_logger()
    try:
        zip_path = Path(snapshot_path)
        components = None
        if only:
            components = set(only.replace(" ", "").split(","))
            valid = {"graph", "registry", "structured", "kg_staging"}
            invalid = components - valid
            if invalid:
                raise click.ClickException(
                    f"Invalid components: {invalid}. Choose from: {', '.join(sorted(valid))}"
                )

        # Analyze snapshot first
        analysis = analyze_snapshot(zip_path, include=components)

        # Show what will be restored
        requested = set(analysis.get("requested_components", []))
        available = set(analysis.get("available_components", []))

        click.echo(f"Snapshot: {analysis['snapshot']}")
        click.echo(f"  Available components: {', '.join(sorted(available))}")
        click.echo(f"  Will restore: {', '.join(sorted(requested))}")

        # Show warnings
        warnings = analysis.get("warnings", [])
        if warnings:
            click.echo("\nWarnings:")
            for warning in warnings:
                click.echo(f"  ⚠️  {warning}")

        if not yes:
            if "graph" in requested:
                env = load_env()
                db_name = env.get("ARTMIND_KG_NEO4J_DATABASE", "neo4j")
                if not click.confirm(
                    f"This will delete all data in Neo4j database '{db_name}'. Continue?"
                ):
                    raise click.Abort()
            elif warnings:
                if not click.confirm("Proceed with restore despite warnings?"):
                    raise click.Abort()
            else:
                if not click.confirm("Proceed with restore?"):
                    raise click.Abort()

        # Perform the actual restore
        click.echo("\nRestoring...")
        result = restore_snapshot_impl(zip_path, include=components)

        # Display results
        click.echo(f"\n✓ Restore complete ({result['elapsed_seconds']}s)")
        for comp in result.get("components_restored", []):
            click.echo(f"  ✓ {comp}")

        # Show errors if any
        if result.get("errors"):
            click.echo("\nErrors encountered:")
            for error in result["errors"]:
                click.echo(f"  ✗ {error}")

        _echo_json(result, compact)

    except click.Abort:
        raise
    except FileNotFoundError as e:
        raise click.ClickException(str(e))
    except Exception as e:
        raise click.ClickException(str(e))


# ── artmind setup ──────────────────────────────────────────────────────────────


@cli.command("init")
def init():
    """Scaffold the run folder (~/.artmind) + data dirs; seed .env, skills, schemas.

    Filesystem-only and idempotent — needs no Neo4j. Run this right after
    install, then edit ~/.artmind/.env and run `artmind setup`.
    """
    try:
        result = scaffold_run_folder()
        click.echo("Run folder:  " + result["run_folder"])
        click.echo("Data dir:    " + result["data_dir"])
        click.echo("Config .env: " + result["env"])
        click.echo(f"Skills:      {result['skills_refreshed']} refreshed from package")
        click.echo(f"opencode:    {result['opencode_refreshed']} refreshed from package")
        click.echo(f"Schemas:     {result['schemas_copied']} refreshed from package")
        click.echo("\nNext: edit " + result["run_folder"] + "/.env, then run `artmind setup`.")
    except Exception as e:
        raise click.ClickException(str(e))


@cli.command("setup")
def setup():
    """Initialize SQLite tables and Neo4j constraints/indexes (idempotent)."""
    try:
        result = setup_all()
        click.echo("SQLite:               " + result["sqlite"])
        click.echo("Neo4j constraints:    " + ", ".join(result["neo4j_constraints"]))
        click.echo("Neo4j indexes:        " + ", ".join(result["neo4j_indexes"]))
        click.echo("Neo4j vector indexes: " + ", ".join(result["neo4j_vector_indexes"]))
        click.echo("Neo4j fulltext indexes: " + ", ".join(result.get("neo4j_fulltext_indexes", [])))
        click.echo("\nSetup complete.")
    except Exception as e:
        raise click.ClickException(str(e))
