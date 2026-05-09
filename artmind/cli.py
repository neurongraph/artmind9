import os
import shutil
import subprocess
import sys
import time
import json
from datetime import datetime
from pathlib import Path

import click
import yaml
from loguru import logger

from artmind import graph_query, vector_query
import artmind.update as update_backend
from artmind.graph_snapshot import export_graph, import_graph
from artmind.harmonizer import harmonize_all, harmonize_schema
from artmind.setup import setup_all
from artmind.dashboard import run_dashboard
from artmind.ingest import (
    _build_file_result_from_db,
    clean_document,
    extract_kg,
    ingest_file,
    ingest_to_kg,
    write_to_graph,
)
from artmind.jobs import (
    _create_job,
    _get_job_results,
    _get_job_status,
    _list_jobs,
)
from artmind.refine_graph import refine_graph
from paths import (
    DOMAIN_SCHEMAS_DIR,
    INGEST_LOG_FILE,
    REFINE_DIR,
    WORKER_LOG,
    WORKER_PID_FILE,
)
from utils.functions import load_env


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


# ── worker helpers ────────────────────────────────────────────────────────────


def _ensure_worker_running() -> None:
    if WORKER_PID_FILE.exists():
        try:
            pid = int(WORKER_PID_FILE.read_text().strip())
            os.kill(pid, 0)
            return  # live worker found
        except (ProcessLookupError, ValueError):
            pass  # stale PID

    worker_script = WORKER_PID_FILE.parent / "artmind" / "worker.py"
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


@click.group()
def cli():
    """Artmind — a knowledge system that synchronizes with your mind."""
    pass


def _echo_json(payload: dict, compact: bool = False) -> None:
    kwargs = {"ensure_ascii": False}
    if compact:
        kwargs["separators"] = (",", ":")
    else:
        kwargs["indent"] = 2
    click.echo(json.dumps(payload, **kwargs))


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


@domains.command("entities_prompt")
@click.argument("domain_name")
def get_entities(domain_name: str):
    """The prompt used to extract entities from a document chunk"""
    data = _load_domain_schema(domain_name)
    click.echo(data.get("entities_prompt", []))


@domains.command("properties_prompt")
@click.argument("domain_name")
def get_properties(domain_name: str):
    """The prompt used to extract properties for the set of entities from a document chunk"""
    data = _load_domain_schema(domain_name)
    click.echo(data.get("properties_prompt", []))


@domains.command("relationships_prompt")
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
def ingest_sync(file_path: str, domain: str | None):
    """Ingest a file or directory synchronously (blocking)."""
    _setup_logger()
    env = load_env()
    image_model = env.get("ARTMIND_IMAGE_MODEL", "gemma4:e4b")
    text_model = env.get("ARTMIND_KG_LLM_MODEL", "ministral-3:14b")
    embed_model = env.get("ARTMIND_KG_EMBEDDINGS_MODEL", "nomic-embed-text:latest")
    chunk_size = int(env.get("ARTMIND_KG_CHUNK_SIZE", "6000"))

    if domain is None:
        domain = _prompt_for_domain()

    path = Path(file_path)
    files = sorted(
        f for f in (path.rglob("*") if path.is_dir() else [path]) if f.is_file()
    )

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
            result = ingest_file(f, image_model, domain, chunk_size=chunk_size)
            if result.get("status") == "ok":
                ok_count += 1
                ingest_to_kg(result, domain, text_model, embed_model, chunk_size)
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
def ingest_async(file_path: str, domain: str | None):
    """Submit a file or directory for background ingestion; returns job_id immediately."""
    _setup_logger()
    if domain is None:
        domain = _prompt_for_domain()

    path = Path(file_path)
    if path.is_dir():
        files = sorted(
            f for f in path.rglob("*")
            if f.is_file()
            and not any(p.startswith(".") for p in f.relative_to(path).parts)
        )
    else:
        files = [path]
    if not files:
        raise click.ClickException(f"No files found in {path}")

    batch_files = [str(f.resolve()) for f in files]
    job_id = _create_job(batch_files, domain=domain)
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


@ingest.command("dashboard")
def ingest_dashboard():
    """Show a live realtime status dashboard of all async jobs."""
    run_dashboard()


@ingest.command("extract_kg")
@click.argument("document_name")
@click.option("--domain", required=True, help="Domain the document belongs to")
def ingest_extract_kg(document_name: str, domain: str) -> None:
    """Re-run or resume KG extraction for a document (skips already-ok chunks).

    DOCUMENT_NAME is the registered filename (e.g. myfile.pdf).
    Extraction results are merged into doc-level JSON files ready for write_to_graph.
    """
    _setup_logger()
    env = load_env()
    text_model = env.get("ARTMIND_KG_LLM_MODEL", "ministral-3:14b")
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
    doc_kg_dir = extract_kg(file_result, domain, text_model, embed_model)
    if doc_kg_dir:
        logger.info("extract_kg complete — merged JSON in {}", doc_kg_dir)
    else:
        raise click.ClickException("extract_kg failed — check logs for details")


@ingest.command("write_to_graph")
@click.argument("document_name")
@click.option("--domain", required=True, help="Domain the document belongs to")
def ingest_write_to_graph(document_name: str, domain: str) -> None:
    """Write already-extracted KG JSON to Neo4j (re-run after fixing Neo4j issues).

    DOCUMENT_NAME is the registered filename (e.g. myfile.pdf).
    Requires that extract_kg (or ingest sync) has already produced the merged JSON files.
    """
    _setup_logger()
    from paths import KG_DIR

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
    ok = write_to_graph(doc_kg_dir)
    if ok:
        logger.info("write_to_graph complete")
    else:
        raise click.ClickException("write_to_graph failed — check logs for Neo4j errors")


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
def ingest_refine_graph(
    domain: str | None,
    name_filter: str | None,
    model: str | None,
    threshold: float,
    dry_run: bool,
    output_file: str | None,
    from_file: str | None,
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
    resolved_model = model or env.get("ARTMIND_KG_LLM_MODEL", "ministral-3:14b")

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


# ── artmind query ──────────────────────────────────────────────────────────────


@cli.group()
def query():
    """Query the knowledge graph and vector index."""
    pass


@query.group()
def graph():
    """Execute graph queries (metadata, entity listing, pattern1–pattern9)."""
    pass


def _run_graph_pattern(
    pattern: str, domain: str, compact: bool, question: str | None, **kwargs
) -> None:
    try:
        result = graph_query.execute_pattern(
            domain=domain, pattern=pattern, question=question, **kwargs
        )
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    _echo_json(result, compact)


@graph.command("metadata")
@click.option("--domain", required=True, help="Domain to query")
@click.option("--compact", is_flag=True, help="Emit compact JSON")
def graph_metadata_cmd(domain: str, compact: bool) -> None:
    """Return graph schema metadata (labels, properties, relationship types)."""
    _echo_json(graph_query.graph_metadata(domain), compact)


@graph.command("entity_listing")
@click.option("--domain", required=True, help="Domain to query")
@click.option("--nameFilter", "name_filter", default=None, help="Fuzzy match entity names (case-insensitive substring)")
@click.option("--countAll", "count_all", is_flag=True, help="Include total unfiltered entity count in output")
@click.option("--compact", is_flag=True, help="Emit compact JSON")
def graph_entity_listing_cmd(domain: str, name_filter: str | None, count_all: bool, compact: bool) -> None:
    """Return entity names grouped by label/type."""
    _echo_json(graph_query.entity_listing(domain, name_filter=name_filter, count_all=count_all), compact)


@graph.command("pattern1")
@click.option("--domain", required=True, help="Domain to query")
@click.option("--entityClass", "entity_class", required=True, help="Entity class label (e.g. CHARACTER, LOCATION)")
@click.option("--compact", is_flag=True, help="Emit compact JSON")
@click.argument("question", required=False)
def graph_pattern1(domain: str, entity_class: str, compact: bool, question: str | None) -> None:
    """List entities of a class."""
    _run_graph_pattern("pattern1", domain, compact, question, entityClass=entity_class)


@graph.command("pattern2")
@click.option("--domain", required=True, help="Domain to query")
@click.option("--entityNameList", "entity_name_list", multiple=True, required=True, help="Entity name (repeatable)")
@click.option("--compact", is_flag=True, help="Emit compact JSON")
@click.argument("question", required=False)
def graph_pattern2(domain: str, entity_name_list: tuple, compact: bool, question: str | None) -> None:
    """Info on one or more named entities."""
    _run_graph_pattern("pattern2", domain, compact, question, entityNameList=entity_name_list)


@graph.command("pattern3")
@click.option("--domain", required=True, help="Domain to query")
@click.option("--entityNameList", "entity_name_list", multiple=True, required=True, help="Entity name (repeatable)")
@click.option("--compact", is_flag=True, help="Emit compact JSON")
@click.argument("question", required=False)
def graph_pattern3(domain: str, entity_name_list: tuple, compact: bool, question: str | None) -> None:
    """Entity + lightweight relationship summary."""
    _run_graph_pattern("pattern3", domain, compact, question, entityNameList=entity_name_list)


@graph.command("pattern4")
@click.option("--domain", required=True, help="Domain to query")
@click.option("--entityClass", "entity_class", required=True, help="Entity class label")
@click.option("--entityName", "entity_name", required=True, help="Entity name (substring match)")
@click.option("--compact", is_flag=True, help="Emit compact JSON")
@click.argument("question", required=False)
def graph_pattern4(
    domain: str, entity_class: str, entity_name: str, compact: bool, question: str | None
) -> None:
    """Entity + full neighborhood."""
    _run_graph_pattern(
        "pattern4", domain, compact, question, entityClass=entity_class, entityName=entity_name
    )


@graph.command("pattern5")
@click.option("--domain", required=True, help="Domain to query")
@click.option("--entityClass1", "entity_class1", required=True, help="Class of first entity")
@click.option("--entityClass2", "entity_class2", required=True, help="Class of second entity")
@click.option("--entityName1", "entity_name1", required=True, help="Name of first entity")
@click.option("--entityName2", "entity_name2", required=True, help="Name of second entity")
@click.option(
    "--mode",
    type=click.Choice(["shortest", "all"]),
    default="shortest",
    show_default=True,
    help="Path mode",
)
@click.option("--compact", is_flag=True, help="Emit compact JSON")
@click.argument("question", required=False)
def graph_pattern5(
    domain: str,
    entity_class1: str,
    entity_class2: str,
    entity_name1: str,
    entity_name2: str,
    mode: str,
    compact: bool,
    question: str | None,
) -> None:
    """Paths between two entities (shortest or all within depth 5)."""
    _run_graph_pattern(
        "pattern5", domain, compact, question,
        entityClass1=entity_class1,
        entityClass2=entity_class2,
        entityName1=entity_name1,
        entityName2=entity_name2,
        mode=mode,
    )


@graph.command("pattern6")
@click.option("--domain", required=True, help="Domain to query")
@click.option("--entityName1", "entity_name1", required=True, help="Name of first entity")
@click.option("--entityName2", "entity_name2", required=True, help="Name of second entity")
@click.option("--compact", is_flag=True, help="Emit compact JSON")
@click.argument("question", required=False)
def graph_pattern6(
    domain: str, entity_name1: str, entity_name2: str, compact: bool, question: str | None
) -> None:
    """Direct relationships between two entities."""
    _run_graph_pattern(
        "pattern6", domain, compact, question,
        entityName1=entity_name1, entityName2=entity_name2,
    )


@graph.command("pattern7")
@click.option("--domain", required=True, help="Domain to query")
@click.option("--searchTerm", "search_term", required=True, help="Substring match on name or description")
@click.option("--limit", type=int, default=10, show_default=True, help="Max results")
@click.option("--compact", is_flag=True, help="Emit compact JSON")
@click.argument("question", required=False)
def graph_pattern7(
    domain: str, search_term: str, limit: int, compact: bool, question: str | None
) -> None:
    """Search entities by name or description fragment."""
    _run_graph_pattern("pattern7", domain, compact, question, searchTerm=search_term, limit=limit)


@graph.command("pattern8")
@click.option("--domain", required=True, help="Domain to query")
@click.option("--entityClass", "entity_class", required=True, help="Class of entities to return")
@click.option("--entityName", "entity_name", required=True, help="Name of the connected entity")
@click.option("--compact", is_flag=True, help="Emit compact JSON")
@click.argument("question", required=False)
def graph_pattern8(
    domain: str, entity_class: str, entity_name: str, compact: bool, question: str | None
) -> None:
    """Entities of class X connected to entity Y."""
    _run_graph_pattern(
        "pattern8", domain, compact, question, entityClass=entity_class, entityName=entity_name
    )


@graph.command("pattern9")
@click.option("--domain", required=True, help="Domain to query")
@click.option("--entityClass", "entity_class", required=True, help="Entity class label")
@click.option("--topN", "top_n", type=int, default=5, show_default=True, help="Number of top entities")
@click.option("--compact", is_flag=True, help="Emit compact JSON")
@click.argument("question", required=False)
def graph_pattern9(
    domain: str, entity_class: str, top_n: int, compact: bool, question: str | None
) -> None:
    """Top-N entities of a class by connection count."""
    _run_graph_pattern("pattern9", domain, compact, question, entityClass=entity_class, topN=top_n)


@query.command("vector_text")
@click.option("--domain", required=True, help="Domain to query")
@click.option("--topK", "top_k", type=int, default=5, show_default=True)
@click.option("--compact", is_flag=True, help="Emit compact JSON")
@click.argument("question")
def vector_text(domain: str, top_k: int, compact: bool, question: str) -> None:
    """Search source text using vector embeddings and keyword matching combined via RRF."""
    _echo_json(vector_query.vector_text_search(domain, question, top_k), compact)


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
    click.echo(f"  neo4j documents: {result['neo4j_documents']}")
    click.echo(f"  neo4j chunks: {result['neo4j_chunks']}")
    click.echo(f"  neo4j orphan entities: {result['neo4j_orphan_entities']}")
    if result["neo4j_error"]:
        raise click.ClickException(f"Neo4j cleanup failed: {result['neo4j_error']}")


# ── artmind update ─────────────────────────────────────────────────────────────


@cli.group()
def update():
    """Add and update knowledge graph facts from natural language."""
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


# ── artmind setup ──────────────────────────────────────────────────────────────


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
