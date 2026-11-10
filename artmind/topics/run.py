"""`artmind query topics plan|run` (spec 2026-11-10 §5.2): show a saved topic plan, and run
its surveys live. Read-only, no LLM. Rows are ordinary survey evidence, each naming its cell, ordered by rank across cells;
the main list is `rows`, so `ARTMIND_OUTPUT_BUDGET` cuts it and `query page` reads on."""
from __future__ import annotations

from artmind import brief
from artmind.topics import grid
from artmind.topics.plan import PlanError
from artmind.topics.plan_store import LoadedPlan

#: Passages per document a full-shape run row carries.
FULL_PASSAGES = 3
#: A brief row's passage: shorter than survey's lead passages, so a plan's many cells fit the
#: output budget (spec 2026-11-10 §13 "plan output vs budget"); `--shape full` has the whole text.
BRIEF_PASSAGE_CHARS = 500


def _select(cells: list[grid.Cell], subject: int | None, cell: str | None) -> list[grid.Cell]:
    if cell:
        want = cell.strip()
        cells = [c for c in cells if want in (c.period, c.domain, c.label())]
    return cells


def plan_cells(loaded: LoadedPlan, subject: int | None = None, *, session=None):
    spec = loaded.spec
    subjects = list(spec.subjects)
    if subject is not None and not 1 <= subject <= len(subjects):
        raise PlanError(f"--subject must be between 1 and {len(subjects)} (plan {spec.slug!r} has "
                        f"{len(subjects)} subject(s))")
    cells, report = grid.plan_cells(subjects, list(spec.domains), spec.by, spec.split, spec.per_cell,
                                    spec.max_docs, session=session)
    if subject is not None:
        cells = [c for c in cells if c.subject == subject]
    return subjects, cells, report


def _doc_row(cell: grid.Cell, rank: int, doc: dict, shape: str, seen: dict) -> dict:
    row = {"cell": cell.label(), "rank": rank, "doc_id": doc.get("doc_id"),
           "title": brief.doc_title(doc.get("title"), doc.get("name")), "domain": doc.get("domain"),
           "date": doc.get("date"), "status": doc.get("status")}
    first = seen.get(doc.get("doc_id"))
    if first is not None:
        row["seen_in"] = first  # shown under its first cell; a line row here
        return row
    seen[doc.get("doc_id")] = cell.label()
    passages = doc.get("passages") or []
    if shape == "brief":
        p = passages[0] if passages else None
        row["passage"] = ({"passage_id": p.get("id"), "chunk_id": p.get("chunk_id"),
                           "text": brief.cut_keep(p.get("text"), BRIEF_PASSAGE_CHARS),
                           "relevance": p.get("relevance")} if p else None)
    else:
        row["summary"] = doc.get("summary")
        row["passages"] = [{"passage_id": p.get("id"), "chunk_id": p.get("chunk_id"), "text": p.get("text"),
                            "relevance": p.get("relevance")} for p in passages[:FULL_PASSAGES]]
    return row


def run_plan(loaded: LoadedPlan, *, subject: int | None = None, cell: str | None = None, shape: str = "full",
             search=None, session=None) -> dict:
    if shape not in brief.SHAPES:
        raise PlanError(f"--shape must be one of {', '.join(brief.SHAPES)}")
    spec = loaded.spec
    subjects, cells, report = plan_cells(loaded, subject, session=session)
    cells = _select(cells, subject, cell)
    if cell and not cells:
        raise PlanError(f"--cell {cell!r} matches no cell of plan {spec.slug!r}; "
                        f"see `artmind query topics plan {spec.slug}`")
    passages = 1 if shape == "brief" else FULL_PASSAGES
    materials, per_cell = grid.gather(subjects, cells, list(spec.domains), passages=passages, search=search)
    # Round-robin by rank across cells (every cell's rank 1, then rank 2, ...): an output-budget
    # cut keeps the top documents of every cell instead of dropping the last cells whole.
    rows, seen = [], {}
    depth = max((len(c.doc_ids) for c in cells), default=0)
    for rank in range(1, depth + 1):
        for c in cells:
            if rank <= len(c.doc_ids):
                doc_id = c.doc_ids[rank - 1]
                rows.append(_doc_row(c, rank, per_cell[(c.label(), doc_id)], shape, seen))
    warnings = []
    if report["cut"]:
        warnings.append(f"the plan's cells want {report['wanted']} documents; cut fairly to {report['max_docs']} "
                        "(max_docs); a cell marked partial has more")
    if any(c.period == "undated" and c.matching for c in cells):
        warnings.append("undated documents match a subject; they cannot be windowed by period and are not gathered")
    if any(c.at_least for c in cells):
        warnings.append(f"keyword counts are a floor (more than {grid.HIT_LIMIT} matching passages)")
    partial = [c.label() for c in cells if c.matching > len(c.doc_ids) and c.period != "undated"]
    return {
        "slug": spec.slug, "topic": spec.topic, "version": spec.version, "domains": list(spec.domains),
        "subjects": [{"n": i, "text": s} for i, s in enumerate(subjects, 1)],
        "slices": {"by": spec.by, "split": spec.split},
        "parameters": {"perCell": spec.per_cell, "maxDocs": spec.max_docs, "shape": shape,
                       "subject": subject, "cell": cell},
        "warnings": warnings,
        "cells": [c.to_json() for c in cells],
        "documents": len(materials),
        **({"complete": False, "partial_cells": partial} if partial else {"complete": True}),
        "rows": rows,
    }


def plan_payload(loaded: LoadedPlan, *, session=None, live: bool = True) -> dict:
    """The plan, its SKILL.md and, per cell, `matching` at build vs now."""
    spec = loaded.spec
    out = {"plan": {**spec.summary(), "built": spec.built or None}, "skill": loaded.skill_md,
           "path": str(loaded.path), "warnings": []}
    built = {(g.get("subject"), g.get("period"), g.get("domain")): g for g in (spec.built or {}).get("grid") or []
             if isinstance(g, dict)}
    if not live:
        return out
    try:
        _, cells, _ = plan_cells(loaded, session=session)
    except PlanError:
        raise
    except Exception as exc:  # an unreachable graph: the plan is still shown
        out["warnings"].append(f"cells not counted (graph unavailable: {exc})")
        return out
    rows = []
    for c in cells:
        b = built.get((c.subject, c.period, c.domain)) or {}
        rows.append({"label": c.label(), "subject": c.subject, "period": c.period, "domain": c.domain,
                     "matching_built": b.get("matching"), "taken_built": b.get("taken"), "matching_now": c.matching})
    out["cells"] = rows
    return out


def list_payload(plans: list[LoadedPlan], invalid: list[dict]) -> dict:
    return {"plans": [p.spec.summary() for p in plans], "invalid": invalid, "rows_total": len(plans),
            "truncated": False}
