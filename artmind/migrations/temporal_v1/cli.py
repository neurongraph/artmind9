"""`artmind migrate status` and `artmind migrate temporal`."""
from __future__ import annotations

from pathlib import Path

import click

from artmind import data_model
from artmind.cli_conventions import Change, Effect, compact_option, dry_run_option, with_effect
from artmind.migrations.temporal_v1 import steps as steps_mod
from artmind.migrations.temporal_v1.steps import MigrationContext, Step

NEXT_DRY = "artmind migrate temporal --dryRun"


@click.group("migrate")
def migrate_group() -> None:
    """Bring a vault's data to the model this artmind reads (temporary migration pack).

    `status` is read-only and says what is pending, in the committed files and in this
    machine's graph. `temporal --dryRun` shows the plan; `temporal` applies it.
    """


def _vault_root() -> Path | None:
    import paths

    return Path(paths.ARTMIND_VAULT_DIR) if paths.ARTMIND_VAULT_DIR else None


def _open_graph():
    from artmind.graph_query import neo4j_session

    return neo4j_session()


def _context(domain: tuple, dry_run: bool) -> MigrationContext:
    from artmind.cli import _get_available_domains, _parse_domains

    domains = _parse_domains(domain) if domain else _get_available_domains()
    return MigrationContext(domains=domains, session_factory=_open_graph, dry_run=dry_run)


def _pending(ctx: MigrationContext) -> list[tuple[Step, dict]]:
    return [(s, s.pending(ctx) or {}) for s in steps_mod.STEPS]


def _blocked(ctx: MigrationContext) -> list[str]:
    """Why a real run could not start now (none = it can)."""
    from artmind import backfill, setup

    reasons: list[str] = []
    root = _vault_root()
    if root is None:
        reasons.append("not inside a vault")
    else:
        paths = [data_model.MODEL_FILE]
        for step in steps_mod.STEPS:
            paths.extend(step.writes(ctx))
        dirty = sorted({str(p) for p in paths if setup._uncommitted(root, p)})
        if dirty:
            reasons.append("uncommitted changes (commit or restore them first): " + ", ".join(dirty))
    if backfill._worker_running():
        reasons.append("the ingest worker is running (wait for it to finish: artmind ingest job-status)")
    try:
        with ctx.session_factory() as session:
            session.run("RETURN 1").consume()
    except Exception as exc:  # noqa: BLE001 - any failure means the graph cannot be written
        reasons.append(f"Neo4j is not reachable ({type(exc).__name__})")
    return reasons


def _rows(pending: list[tuple[Step, dict]]) -> list[dict]:
    return [{"name": s.name, "pending": p, "done": not p} for s, p in pending]


def _effect(pending: list[tuple[Step, dict]]) -> Effect:
    todo = [s.name for s, p in pending if p]
    if not todo:
        return Effect(summary="Nothing to migrate: every step is done", travels=True)
    return Effect(
        summary=f"Migrate {len(todo)} step(s) to data model {data_model.CURRENT}: {', '.join(todo)}",
        changes=[Change("data_migration", len(todo), todo)],
        reversible=False, undo="git restore the committed files; the graph has no undo",
        travels=True, records=[str(data_model.MODEL_FILE.name)],
    )


@migrate_group.command("status")
@click.option("--domain", "domain", multiple=True, help="Domain (repeatable; comma-splittable). Omit for every domain")
@compact_option
def migrate_status(domain: tuple, compact: bool) -> None:
    """What is pending, per step, and which model the files and this graph are at. Read-only."""
    from artmind.cli import _echo_json

    ctx = _context(domain, True)
    pending = _pending(ctx)
    version, graph_version = data_model.recorded(), data_model.read_graph_version()
    warnings = [] if graph_version is not None else ["the graph is unreachable: its model version is unknown"]
    _echo_json({
        "version": version,
        "graph_version": graph_version,
        "current": version == data_model.CURRENT and graph_version == data_model.CURRENT,
        "steps": _rows(pending),
        "blocked": _blocked(ctx),
        "warnings": warnings,
        "next": NEXT_DRY if any(p for _, p in pending) else None,
    }, compact)


@migrate_group.command("temporal")
@click.option("--domain", "domain", multiple=True, help="Domain (repeatable; comma-splittable). Omit for every domain")
@dry_run_option("Report each step's pending work and any blocker; change nothing", legacy=False)
@compact_option
def migrate_temporal(domain: tuple, dry_run: bool, compact: bool) -> None:
    """Migrate this vault's data to the temporal model: every pending step, in order, then re-check.

    A step does only what it counts as pending, so a second run changes nothing and an
    interrupted run resumes. Needs the vault git-clean for the files it writes, the ingest
    worker stopped and Neo4j reachable; a dry run lists these as `blocked`.
    """
    from artmind.cli import _echo_json

    ctx = _context(domain, dry_run)
    before = _pending(ctx)
    effect = _effect(before)
    blocked = _blocked(ctx)
    if dry_run:
        _echo_json(with_effect({"steps": _rows(before), "blocked": blocked}, effect, True), compact)
        return
    if blocked:
        raise click.ClickException("migrate temporal: not started, nothing was changed: " + "; ".join(blocked))
    results = []
    for step, counts in before:
        applied = step.apply(ctx, False) if counts else {}
        results.append({"name": step.name, "pending": counts, "applied": applied})
    after = _pending(ctx)
    for row, (_, left) in zip(results, after):
        row["remaining"], row["done"] = left, not left
    stamped = steps_mod.stamp_if_done(ctx)
    done = all(r["done"] for r in results)
    _echo_json(with_effect({"steps": results, "done": done, "stamped": bool(stamped), "blocked": []}, effect), compact)
    if not done:
        raise SystemExit(1)
