"""`artmind migrate status` and `artmind migrate temporal`."""
from __future__ import annotations

from pathlib import Path

import click

from artmind import data_model
from artmind.cli_conventions import Change, Effect, compact_option, dry_run_option, store_failure, with_effect
from artmind.migrations.temporal_v1 import steps as steps_mod
from artmind.migrations.temporal_v1.steps import MigrationContext, Step

NEXT_DRY = "artmind migrate temporal --dryRun"
PROBE_TIMEOUT_S = 5.0


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


def _probe_graph():
    """A session for the reachability probe only: a down Neo4j fails in seconds."""
    from artmind.graph_query import neo4j_session

    return neo4j_session(timeout=PROBE_TIMEOUT_S)


def _context(domain: tuple, dry_run: bool) -> MigrationContext:
    from artmind.cli import _get_available_domains, _parse_domains

    domains = _parse_domains(domain) if domain else _get_available_domains()
    return MigrationContext(domains=domains, session_factory=_open_graph, dry_run=dry_run)


def _all_domains() -> list[str]:
    from artmind.cli import _get_available_domains

    return _get_available_domains()


def _err(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"


def _pending(ctx: MigrationContext) -> tuple[list[tuple[Step, dict | None]], list[str]]:
    """Each step's pending counts (None when it could not be read) and a warning per failure."""
    out, warnings = [], []
    for s in steps_mod.STEPS:
        try:
            out.append((s, s.pending(ctx) or {}))
        except Exception as exc:  # noqa: BLE001 - one unreadable step must not hide the others
            out.append((s, None))
            warnings.append(f"step {s.name}: pending could not be read ({_err(exc)})")
    return out, warnings


def _git(root: Path, *args: str):
    import subprocess

    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True)


def _blocked(ctx: MigrationContext) -> list[str]:
    """Why a real run could not start now (none = it can)."""
    from artmind import backfill

    reasons: list[str] = []
    root = _vault_root()
    if root is None:
        reasons.append("not inside a vault")
    elif _git(root, "rev-parse", "--is-inside-work-tree").returncode != 0:
        reasons.append("the vault is not a git repository (its files must be committed before a migration)")
    else:
        paths = [data_model.MODEL_FILE]
        for step in steps_mod.STEPS:
            paths.extend(step.writes(ctx))
        dirs = sorted({str(p if p.is_dir() else p.parent) for p in paths})
        status = _git(root, "status", "--porcelain", "--", *dirs)
        if status.returncode != 0 or status.stdout.strip():
            reasons.append("uncommitted changes under: " + ", ".join(dirs) + " (commit or restore them first)")
    if backfill.worker_is_running():
        reasons.append("the ingest worker is running (wait for it to finish: artmind ingest job-status)")
    try:
        with _probe_graph() as session:
            session.run("RETURN 1").consume()
    except Exception as exc:  # noqa: BLE001 - any failure means the graph cannot be written
        reasons.append(f"Neo4j is not reachable ({type(exc).__name__})")
    return reasons


def _rows(pending: list[tuple[Step, dict | None]]) -> list[dict]:
    return [{"name": s.name, "pending": p, "done": p == {}, **({"error": "could not be read"} if p is None else {})}
            for s, p in pending]


def _effect(pending: list[tuple[Step, dict | None]]) -> Effect:
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
    pending, warnings = _pending(ctx)
    version = data_model.recorded()
    graph_version = data_model.read_graph_version()  # its own 2 s connect timeout
    if graph_version is None:
        warnings.append("the graph is unreachable: its model version is unknown")
    _echo_json({
        "version": version,
        "graph_version": graph_version,
        "current": version == data_model.CURRENT and graph_version == data_model.CURRENT,
        "steps": _rows(pending),
        "blocked": _blocked(ctx),
        "warnings": warnings,
        "next": NEXT_DRY if any(p or p is None for _, p in pending) else None,
    }, compact)


@migrate_group.command("temporal")
@click.option("--domain", "domain", multiple=True, help="Domain (repeatable; comma-splittable). Omit for every domain")
@dry_run_option("Report each step's pending work and any blocker; change nothing", legacy=False)
@compact_option
def migrate_temporal(domain: tuple, dry_run: bool, compact: bool) -> None:
    """Migrate this vault's data to the temporal model: every pending step, in order, then re-check.

    A step does only what it counts as pending, so a second run changes nothing and an
    interrupted run resumes. Needs the vault git-clean for the files it writes, the ingest
    worker stopped and Neo4j reachable; a dry run lists these as `blocked`. A failed step is
    reported on its row and stops the run (exit 3 for a store failure, else 1); exit 1 also
    when a step is still pending after its apply.
    """
    from artmind.cli import _echo_json

    ctx = _context(domain, dry_run)
    before, warnings = _pending(ctx)
    effect = _effect(before)
    blocked = _blocked(ctx)
    if dry_run:
        _echo_json(with_effect({"steps": _rows(before), "blocked": blocked, "warnings": warnings}, effect, True), compact)
        return
    if blocked:
        raise click.ClickException("migrate temporal: not started, nothing was changed: " + "; ".join(blocked))
    if any(p is None for _, p in before):
        raise click.ClickException("migrate temporal: not started, nothing was changed: " + "; ".join(warnings))
    results, failure = [], None
    for step, counts in before:
        row = {"name": step.name, "pending": counts, "applied": {}}
        results.append(row)
        if counts and failure is None:
            try:
                row["applied"] = step.apply(ctx, False)
            except Exception as exc:  # noqa: BLE001 - report it on the step's row and stop
                row["error"], failure = _err(exc), exc
    after, after_warnings = _pending(ctx)
    for row, (_, left) in zip(results, after):
        row["remaining"], row["done"] = left, left == {}
    done = failure is None and all(r["done"] for r in results)
    stamped = False
    if failure is None:
        # The stamp judges every domain: a run scoped to one must not bless the others.
        everything = MigrationContext(domains=_all_domains(), session_factory=ctx.session_factory, dry_run=False)
        stamped = bool(steps_mod.stamp_if_done(everything))
    _echo_json(with_effect({"steps": results, "done": done, "stamped": stamped, "blocked": [],
                            "warnings": warnings + after_warnings}, effect), compact)
    if failure is not None and store_failure(failure) is not None:
        raise SystemExit(3)
    if not done:
        raise SystemExit(1)
