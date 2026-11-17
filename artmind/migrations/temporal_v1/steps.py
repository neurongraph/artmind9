"""The step registry. A step is judged by facts, not by a log of what ran.

`pending(ctx)` returns counts of what is still to convert (empty = done);
`apply(ctx, dry_run)` converts exactly what `pending` counted, so a second run
is a no-op and an interrupted run resumes. Tasks 33-36 add the real steps.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class MigrationContext:
    """What a step may use: the domains in scope, a way to open this machine's
    graph (`session_factory() -> context manager`), and whether this is a dry run."""

    domains: list[str]
    session_factory: Callable
    dry_run: bool = False


@dataclass
class Step:
    name: str
    pending: Callable[[MigrationContext], dict]
    apply: Callable[[MigrationContext, bool], dict]
    #: Files the step will write, so the run can require them git-clean first.
    writes: Callable[[MigrationContext], list[Path]] = field(default=lambda ctx: [])


STEPS: list[Step] = []


def stamp_if_done(ctx: MigrationContext) -> bool:
    """Stamp the file and this graph current once no step is pending.

    Always called with an unscoped context (every domain): a run limited to one domain
    must not bless the others. Filled in by Task 36."""
    return False
