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
    #: Of those, the dirty ones that are this step's own half-done work (bytes equal to
    #: what it would write): an interrupted run resumes instead of being blocked.
    own_work: Callable[[MigrationContext], list[Path]] = field(default=lambda ctx: [])
    #: Whether what the step changes travels with the vault (committed files). A graph
    #: step does not: each machine migrates its own graph.
    travels: bool = False
    #: The step's part of the run's effect, from its pending counts:
    #: `{"changes": [Change], "records": [str], "undo": str}`. None: the run's generic one.
    effect: Callable[[dict], dict] | None = None


#: Pending keys that inform but never block `done` (and never make a step run).
INFO_KEYS = frozenset({"unparseable", "by_domain", "classes", "files"})


def work(pending: dict | None) -> dict:
    """The part of a step's pending counts that is work to do."""
    return {k: v for k, v in (pending or {}).items() if k not in INFO_KEYS}


def is_done(pending: dict | None) -> bool:
    return pending is not None and not work(pending)


def _graph_steps() -> list[Step]:
    from artmind.migrations.temporal_v1 import graph

    return [
        # Dates fold before supersession ends are restated: a newer document's
        # `_valid_from` is the end an undated edge writes.
        Step("document_dates", graph.document_dates_pending, graph.document_dates_apply),
        # After the fold, so folded values are normalised too; before ends, which compare dates.
        Step("partial_dates", graph.partial_dates_pending, graph.partial_dates_apply),
        # Record files keep the edge's end: a legacy partial `effective` becomes its start.
        Step("record_effective", graph.record_effective_pending, graph.record_effective_apply,
             graph.record_effective_writes, travels=True),
        # Old chunks/passages/summaries and document-dated observations take their document's
        # stated end, and every such node and the document a `_doc_valid_to` copy, before a supersession close (the earlier end wins) and
        # `unsupersede` (restores `_doc_valid_to`) read them.
        Step("chunk_ends", graph.chunk_ends_pending, graph.chunk_ends_apply),
        # Legacy edges get records before ends are restated: `restate_validity` skips an
        # edge with no `record_id`.
        # Its record files are committed with the vault; its edge ties are this graph's.
        Step("supersession_records", graph.supersession_records_pending, graph.supersession_records_apply,
             graph.supersession_records_writes, graph.supersession_records_own_work),
        Step("supersession_ends", graph.supersession_ends_pending, graph.supersession_ends_apply),
        Step("record_time", graph.record_time_pending, graph.record_time_apply),
        Step("old_indexes", graph.old_indexes_pending, graph.old_indexes_apply),
    ]


def _file_steps() -> list[Step]:
    from artmind.migrations.temporal_v1 import meta_rule

    # A committed file, independent of the graph steps: it goes after them.
    return [Step("meta_rule", meta_rule.pending, meta_rule.apply, meta_rule.writes, travels=True)]


def _rekey_steps() -> list[Step]:
    from artmind.migrations.temporal_v1 import rekey

    # Last: after the graph steps, so documents' dates and record time are current before
    # its rebuild. Its files travel; its graph commit happens on each machine (a machine
    # that pulls the re-keyed staging finds `documents_to_commit` pending).
    return [Step(rekey.STEP, rekey.pending, rekey.apply, rekey.writes, rekey.own_work, travels=True,
                 effect=rekey.effect)]


STEPS: list[Step] = _graph_steps() + _file_steps() + _rekey_steps()


def stamp_if_done(ctx: MigrationContext) -> dict:
    """Stamp both markers current once no step is pending; else say what is.

    Always called with an unscoped context (every domain): a run limited to one domain
    must not bless the others. Contract: the file is stamped first, then the graph; if the
    graph stamp raises, the file stays stamped and the caller reports it (a re-run finds the
    file current and stamps the graph). Returns `{"version": N}` when stamped, else
    `{"pending": [{"step": name, "domains": [...]?}, ...]}` and stamps nothing. The file
    stamp travels by git, the graph's does not, so a second machine that pulled a stamped
    `data_model.yaml` still has its own graph steps pending here and gets its graph stamped
    only when they are done."""
    from artmind import data_model

    left: list[dict] = []
    for s in STEPS:
        try:
            counts = s.pending(ctx)
        except Exception as exc:  # noqa: BLE001 - an unreadable step is not done
            left.append({"step": s.name, "error": f"{type(exc).__name__}: {exc}"})
            continue
        if not is_done(counts):
            row: dict = {"step": s.name}
            domains = sorted((counts or {}).get("by_domain") or [])
            if domains:
                row["domains"] = domains
            left.append(row)
    if left:
        return {"pending": left}
    data_model.stamp(data_model.CURRENT)
    with ctx.session_factory() as session:
        data_model.stamp_graph(session, data_model.CURRENT)  # also refreshes the process cache
    return {"version": data_model.CURRENT}
