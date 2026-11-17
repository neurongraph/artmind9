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
    """What a step may use: the domains in scope (None = unscoped: every node, whatever
    its `_domain`; a step that walks staging lists domains itself), a way to open this
    machine's graph (`session_factory() -> context manager`), and whether this is a dry run."""

    domains: list[str] | None
    session_factory: Callable
    dry_run: bool = False


@dataclass
class Step:
    name: str
    pending: Callable[[MigrationContext], dict]
    apply: Callable[[MigrationContext, bool], dict]
    #: Files the step will write, so the run can require them git-clean first.
    writes: Callable[[MigrationContext], list[Path]] = field(default=lambda ctx: [])


def _graph_steps() -> list[Step]:
    from artmind.migrations.temporal_v1 import graph

    return [
        # Dates fold before supersession ends are restated: a newer document's
        # `_valid_from` is the end an undated edge writes.
        Step("document_dates", graph.document_dates_pending, graph.document_dates_apply),
        # After the fold, so folded values are normalised too; before ends, which compare dates.
        Step("partial_dates", graph.partial_dates_pending, graph.partial_dates_apply),
        # Legacy edges get records before ends are restated: `restate_validity` skips an
        # edge with no `record_id`.
        Step("supersession_records", graph.supersession_records_pending, graph.supersession_records_apply,
             graph.supersession_records_writes),
        Step("supersession_ends", graph.supersession_ends_pending, graph.supersession_ends_apply),
        Step("record_time", graph.record_time_pending, graph.record_time_apply),
        Step("old_indexes", graph.old_indexes_pending, graph.old_indexes_apply),
    ]


STEPS: list[Step] = _graph_steps()


def stamp_if_done(ctx: MigrationContext) -> bool:
    """Stamp the file and this graph current once no step is pending.

    Always called with an unscoped context (every domain): a run limited to one domain
    must not bless the others. Filled in by Task 36."""
    return False
