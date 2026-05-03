import shutil
import sys
import time
import json
from pathlib import Path

import click
import yaml
from loguru import logger

from artmind import graph_query, vector_query
from artmind.ingest import (
    _build_file_result_from_db,
    clean_document,
    extract_kg,
    ingest_file,
    ingest_to_kg,
    write_to_graph,
)
from paths import (
    DOMAIN_SCHEMAS_DIR,
    INGEST_LOG_FILE,
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
    """List all available domain schemas."""
    domains_list = _get_available_domains()
    for d in domains_list:
        click.echo(d)


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


@domains.command("entities")
@click.argument("domain_name")
def list_domain_entities(domain_name: str):
    """List entity classes declared in a domain schema."""
    data = _load_domain_schema(domain_name)
    click.echo(data.get("entities", []))


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


@domains.command("relationships")
@click.argument("domain_name")
def list_domain_relationships(domain_name: str):
    """List relationship types declared in a domain schema."""
    data = _load_domain_schema(domain_name)
    click.echo(data.get("relationships", []))


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


@query.command("vector")
@click.option("--domain", required=True, help="Domain to query")
@click.option("--topK", "top_k", type=int, default=5, show_default=True)
@click.option("--compact", is_flag=True, help="Emit compact JSON")
@click.argument("question")
def vector(domain: str, top_k: int, compact: bool, question: str) -> None:
    """Search DocChunk nodes by embedding similarity."""
    _echo_json(vector_query.vector_search(domain, question, top_k), compact)


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
