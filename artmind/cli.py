import shutil
import sys
import time
from pathlib import Path

import click
import yaml
from loguru import logger

from artmind.ingest import (
    ingest_file,
    ingest_to_kg,
)
from paths import (
    DOMAIN_SCHEMAS_DIR,
    ENV_FILE,
    INGEST_LOG_FILE,
)
from utils.functions import load_env, run_command


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


def prompt_for_domain() -> str:
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
            result = ingest_file(f, image_model, domain)
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


# @ingest.command("async")
# @click.argument("file_path", type=click.Path(exists=True))
# @click.option("--domain", default=None, help="Domain to assign (prompted if omitted)")
# def ingest_async_cmd(file_path: str, domain: str | None):
#     """Submit a file or directory for async ingestion; returns job_id immediately."""
#     if domain is None:
#         domain = _prompt_for_domain()

#     path = Path(file_path)
#     files = sorted(
#         f for f in (path.rglob("*") if path.is_dir() else [path]) if f.is_file()
#     )

#     if not files:
#         raise click.ClickException(f"No files found in {path}")

#     batch_files = [str(f.resolve()) for f in files]
#     job_id = _create_job(batch_files, domain=domain)

#     _ensure_worker_running()

#     click.echo(json.dumps({
#         "job_id": job_id,
#         "domain": domain,
#         "file_count": len(batch_files),
#         "submitted_at": datetime.now().isoformat(),
#         "message": f"Job submitted with {len(batch_files)} file(s)"
#     }))


# @ingest.command("status")
# @click.argument("job_id")
# def ingest_status_cmd(job_id: str):
#     """Check the status of an ingestion job."""
#     job_data = _get_job_status(job_id)
#     if not job_data:
#         raise click.ClickException(f"Job not found: {job_id}")

#     progress = f"{job_data['processed_count']}/{job_data['file_count']}"
#     click.echo(json.dumps({
#         "job_id": job_id,
#         "status": job_data["status"],
#         "progress": progress,
#         "processed_count": job_data["processed_count"],
#         "file_count": job_data["file_count"],
#         "current_file": job_data["current_file"],
#         "queued_at": job_data["queued_at"],
#         "started_at": job_data["started_at"],
#         "completed_at": job_data["completed_at"],
#     }))


# @ingest.command("results")
# @click.argument("job_id")
# def ingest_results_cmd(job_id: str):
#     """Retrieve detailed results of a completed ingestion job."""
#     results = _get_job_results(job_id)
#     if not results:
#         raise click.ClickException(f"Job not found: {job_id}")

#     click.echo(json.dumps(results, indent=2))


# @ingest.command("list-jobs")
# @click.option("--status", default=None, help="Filter by status (queued, processing, completed, failed)")
# def ingest_list_jobs_cmd(status: str | None):
#     """List ingestion jobs."""
#     jobs = _list_jobs(status_filter=status)
#     if not jobs:
#         click.echo("No jobs found")
#         return

#     for job in jobs:
#         progress = f"{job['processed_count']}/{job['file_count']}"
#         click.echo(f"  {job['job_id'][:8]}... | {job['status']:12} | {progress:6} | {job['queued_at']}")


# @ingest.command("monitor")
# @click.argument("job_id", required=False)
# def ingest_monitor_cmd(job_id: str | None):
#     """Monitor an asynchronous ingestion job using a Rich dashboard.
#     If job_id is omitted, it will automatically monitor the most recent active job."""
#     from rich.live import Live
#     from rich.table import Table
#     from rich.progress import Progress, BarColumn, TextColumn, TimeElapsedColumn
#     from rich.panel import Panel
#     from rich.console import Group
#     from rich import box
#     import time

#     if not job_id:
#         active_jobs = _list_jobs(status_filter="processing")
#         if not active_jobs:
#             active_jobs = _list_jobs(status_filter="queued")

#         if active_jobs:
#             job_id = active_jobs[0]["job_id"]
#         else:
#             recent_jobs = _list_jobs()
#             if recent_jobs:
#                 job_id = recent_jobs[0]["job_id"]
#             else:
#                 click.echo("No ingestion jobs found to monitor.")
#                 return

#     def generate_dashboard(job_data):
#         if not job_data:
#             return Panel("[red]Job not found[/red]", title="Error")

#         status = job_data["status"]
#         processed = job_data["processed_count"]
#         total = job_data["file_count"]
#         current_file = job_data.get("current_file") or "Waiting for worker..."
#         domain = job_data["domain"]

#         status_color = "yellow"
#         if status == "completed":
#             status_color = "green"
#         elif status == "failed":
#             status_color = "red"

#         progress = Progress(
#             TextColumn("[bold blue]{task.percentage:>3.0f}%"),
#             BarColumn(bar_width=40),
#             TextColumn("[green]{task.completed}/{task.total} files"),
#             TimeElapsedColumn(),
#         )
#         progress.add_task("ingest", total=total, completed=processed)

#         table = Table(box=box.SIMPLE, show_header=False)
#         table.add_column("Key", style="cyan")
#         table.add_column("Value")

#         table.add_row("Job ID", job_id)
#         table.add_row("Domain", domain)
#         table.add_row("Status", f"[{status_color}]{status.upper()}[/{status_color}]")
#         table.add_row("Current File", f"[bold magenta]{current_file}[/bold magenta]")
#         if job_data.get("error_message"):
#             table.add_row("Error", f"[red]{job_data['error_message']}[/red]")

#         results_data = _get_job_results(job_id)
#         files_panel = None
#         if results_data and "files" in results_data and results_data["files"]:
#             files_table = Table(box=box.SIMPLE)
#             files_table.add_column("Filename", style="cyan")
#             files_table.add_column("Docling", justify="center")
#             files_table.add_column("Neo4j KG", justify="center")

#             display_files = results_data["files"][-10:]
#             for f in display_files:
#                 doc_status = "[green]✓[/green]" if f.get("status") == "ok" else "[red]✗[/red]"
#                 kg_status = "[green]✓[/green]" if f.get("kg_status") == "ok" else "[red]✗[/red]"
#                 if "kg_status" not in f and f.get("status") != "ok":
#                     kg_status = "-"
#                 elif "kg_status" not in f:
#                     kg_status = "[yellow]?[/yellow]"
#                 files_table.add_row(f.get("filename", "unknown"), doc_status, kg_status)

#             title = "Processed Files" if len(results_data["files"]) <= 10 else f"Processed Files (Last 10 of {len(results_data['files'])})"
#             files_panel = Panel(files_table, title=title, border_style="blue")

#         elements = [table, Panel(progress, title="Progress", border_style="blue")]
#         if files_panel:
#             elements.append(files_panel)

#         group = Group(*elements)
#         return Panel(group, title="Artmind Ingestion Monitor", border_style="cyan")

#     initial_data = _get_job_status(job_id)
#     if not initial_data:
#         click.echo(f"Job not found: {job_id}")
#         return

#     try:
#         with Live(generate_dashboard(initial_data), refresh_per_second=2) as live:
#             while True:
#                 job_data = _get_job_status(job_id)
#                 live.update(generate_dashboard(job_data))
#                 if not job_data or job_data["status"] in ("completed", "failed"):
#                     break
#                 time.sleep(1)
#     except KeyboardInterrupt:
#         pass
