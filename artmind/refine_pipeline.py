"""Refine pipeline: one ordered entry point for all graph refinement steps.

The five refinement operations have hard dependencies, so their sequence is
encoded here rather than left to operator memory:

1. time          — normalize_time: canonical valid_from/valid_to must exist
                   before anything reasons about currency
2. supersession  — detect_supersession: stamps valid_to on superseded content;
                   must precede conflicts (superseded claims are history, not
                   live disagreements) and consolidation (HISTORICAL flags)
3. merge         — refine_graph: entity merges must precede conflicts (claims
                   about the same entity must meet on one node) and
                   consolidation (don't pay LLM calls on soon-merged entities)
4. conflicts     — detect_conflicts: must precede consolidation so its
                   skip-open-conflict gate has conflicts to see
5. consolidate   — consolidate_descriptions: benefits from all of the above
6. embed         — one embedding sweep at the end (merges and rewrites both
                   invalidate embeddings)

Two phases, mirroring the dry-run/apply workflow of the individual commands:

- propose (default): steps 1-2 run for real (deterministic, additive,
  idempotent); steps 3-5 run as dry-runs; everything lands in ONE report
  under data/refine/pipeline/<domain>/ with sub-proposal files alongside.
- apply --from-file <report>: re-runs 1-2 (idempotent), applies the vetted
  merge/conflict proposals from the report's sub-files, runs consolidation
  live, then the embed sweep. Editing the sub-files before applying is the
  review mechanism, exactly as with refine-graph / detect-conflicts alone.
- apply without --from-file: one-shot propose+apply for trusted automation.
"""
import json
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger

from artmind.conflicts import detect_conflicts
from artmind.consolidate import consolidate_descriptions
from artmind.graph_query import neo4j_session
from artmind.ingest import embed_entities_backfill
from artmind.refine_graph import refine_graph
from artmind.temporal import detect_supersession, normalize_time
from paths import REFINE_DIR
from utils.functions import load_env, resolve_llm_model


PIPELINE_STEPS = ("time", "supersession", "merge", "conflicts", "consolidate", "embed")


def resolve_steps(steps: "list[str] | None") -> list[str]:
    """Validate a step subset and return it in canonical pipeline order."""
    if not steps:
        return list(PIPELINE_STEPS)
    requested = {s.strip() for s in steps if s.strip()}
    unknown = requested - set(PIPELINE_STEPS)
    if unknown:
        raise ValueError(
            f"Unknown step(s): {sorted(unknown)}; valid steps: {', '.join(PIPELINE_STEPS)}"
        )
    return [s for s in PIPELINE_STEPS if s in requested]


def _run_dir(domain: str, now: str) -> Path:
    d = REFINE_DIR / "pipeline" / domain / now.replace(":", "").replace("+", "_")
    d.mkdir(parents=True, exist_ok=True)
    return d


def _null_embeddings_for_canonicals(domain: str, canonicals: "list[str]") -> int:
    """Null embeddings of merge-target entities so the embed sweep refreshes them.

    refine_graph merges alias properties into the canonical entity but leaves
    its pre-merge embedding in place; backfill only fills NULLs, so without
    this the canonical would keep a stale vector.
    """
    if not canonicals:
        return 0
    with neo4j_session() as session:
        rec = session.run(
            """
            MATCH (e:Entity)
            WHERE (e.domain = $domain OR e.domain STARTS WITH ($domain + '.'))
              AND e.name IN $names
            SET e.embedding = null
            RETURN count(e) AS n
            """,
            domain=domain,
            names=canonicals,
        ).single()
        return rec["n"] if rec else 0


def run_pipeline(
    domain: str,
    apply: bool = False,
    from_file: "str | None" = None,
    steps: "list[str] | None" = None,
    model: "str | None" = None,
    merge_threshold: float = 0.7,
    conflict_sim_threshold: float = 0.75,
    max_pairs: int = 200,
    sample_consolidations: int = 3,
    consolidate_limit: "int | None" = None,
) -> dict:
    """Run the refine pipeline for one domain. See module docstring for phases."""
    env = load_env()
    resolved_model = resolve_llm_model(env, model)
    selected = resolve_steps(steps)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    mode = "apply" if (apply or from_file) else "propose"

    prior: dict = {}
    if from_file:
        prior = json.loads(Path(from_file).read_text(encoding="utf-8"))
        if prior.get("domain") != domain:
            raise ValueError(
                f"--from-file report is for domain {prior.get('domain')!r}, not {domain!r}"
            )

    run_dir = _run_dir(domain, now)
    report: dict = {
        "domain": domain,
        "command": "refine_pipeline",
        "mode": mode,
        "model": resolved_model,
        "started_at": now,
        "steps": {},
    }

    # ── 1. time + 2. supersession: deterministic, additive, idempotent ────────
    if "time" in selected:
        r = normalize_time(domain, dry_run=False)
        report["steps"]["time"] = r
        logger.info("pipeline[{}] time: {}", domain, r)
    if "supersession" in selected:
        r = detect_supersession(domain, dry_run=False)
        report["steps"]["supersession"] = r
        logger.info("pipeline[{}] supersession: {}", domain, r)

    # ── 3. merge ───────────────────────────────────────────────────────────────
    applied_canonicals: list[str] = []
    if "merge" in selected:
        if from_file:
            merges_file = prior.get("steps", {}).get("merge", {}).get("proposals_file")
            if merges_file and Path(merges_file).exists():
                merge_report = refine_graph(
                    domain=domain,
                    name_filter=None,
                    model=resolved_model,
                    similarity_threshold=merge_threshold,
                    dry_run=False,
                    output_file=None,
                    from_file=Path(merges_file),
                )
                applied_canonicals = sorted(
                    set(merge_report.get("proposed_merges", {}).values())
                )
            else:
                merge_report = {"skipped": "no proposals_file in report"}
        else:
            merges_file = run_dir / "merges.json"
            merge_report = refine_graph(
                domain=domain,
                name_filter=None,
                model=resolved_model,
                similarity_threshold=merge_threshold,
                dry_run=(mode == "propose"),
                output_file=merges_file,
                from_file=None,
            )
            merge_report["proposals_file"] = str(merges_file)
            if mode == "apply":
                applied_canonicals = sorted(
                    set(merge_report.get("proposed_merges", {}).values())
                )
        report["steps"]["merge"] = merge_report

    # ── 4. conflicts ───────────────────────────────────────────────────────────
    if "conflicts" in selected:
        if from_file:
            conflicts_file = prior.get("steps", {}).get("conflicts", {}).get("proposals_file")
            if conflicts_file and Path(conflicts_file).exists():
                conflict_report = detect_conflicts(
                    domains=[domain], model=resolved_model, from_file=Path(conflicts_file)
                )
            else:
                conflict_report = {"skipped": "no proposals_file in report"}
        else:
            conflicts_file = run_dir / "conflicts.json"
            conflict_report = detect_conflicts(
                domains=[domain],
                sim_threshold=conflict_sim_threshold,
                max_pairs=max_pairs,
                model=resolved_model,
                dry_run=(mode == "propose"),
                output_file=conflicts_file,
            )
            conflict_report["proposals_file"] = str(conflicts_file)
        report["steps"]["conflicts"] = conflict_report

    # ── 5. consolidate ─────────────────────────────────────────────────────────
    # Propose shows a bounded sample (LLM cost control); apply runs live —
    # consolidation is chunk-set-idempotent and conflict-gated, so proposals
    # don't need per-entity vetting the way merges do.
    if "consolidate" in selected:
        consolidate_report = consolidate_descriptions(
            domain=domain,
            limit=(sample_consolidations if mode == "propose" else consolidate_limit),
            model=model,
            dry_run=(mode == "propose"),
        )
        counts = consolidate_report.get("counts", {})
        consolidate_report["candidates_total"] = counts.get("consolidate", 0) + counts.get(
            "skipped_over_limit", 0
        )
        report["steps"]["consolidate"] = consolidate_report

    # ── 6. embed sweep ─────────────────────────────────────────────────────────
    if "embed" in selected and mode == "apply":
        nulled = _null_embeddings_for_canonicals(domain, applied_canonicals)
        try:
            embedded = embed_entities_backfill(domain)["entities_embedded"]
        except Exception as exc:
            logger.warning("pipeline[{}] embed backfill failed: {}", domain, exc)
            embedded = 0
        report["steps"]["embed"] = {"canonicals_nulled": nulled, "entities_embedded": embedded}

    report_file = run_dir / "pipeline_report.json"
    report_file.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    report["report_file"] = str(report_file)
    if mode == "propose":
        report["apply_with"] = (
            f"uv run artmind ingest refine-pipeline --domain {domain} "
            f"--from-file {report_file}"
        )
    return report
