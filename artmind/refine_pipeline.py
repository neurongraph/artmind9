"""Refine pipeline: one ordered entry point for all graph refinement steps.

The refinement operations have hard dependencies, so their sequence is
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
                   skip-open-conflict gate has conflicts to see. With one
                   domain this pairs entities WITHIN it; with 2+ domains an
                   additional cross-domain pass pairs entities BETWEEN them
                   (candidate_pairs is cross-only for multi-domain input) —
                   and because every domain's merge step ran first, the
                   merge-before-cross-conflicts precondition holds by
                   construction.
5. consolidate   — consolidate_descriptions: benefits from all of the above
6. embed         — one embedding sweep at the end (merges and rewrites both
                   invalidate embeddings)

Two phases, mirroring the dry-run/apply workflow of the individual commands:

- propose (default): steps 1-2 run for real (deterministic, additive,
  idempotent); steps 3-5 run as dry-runs; everything lands in ONE report
  under data/refine/pipeline/ with per-domain sub-proposal files alongside.
- apply --from-file <report>: re-runs 1-2 (idempotent), applies the vetted
  merge/conflict proposals from the report's sub-files, runs consolidation
  live, then the embed sweep. Editing the sub-files before applying is the
  review mechanism, exactly as with refine-graph / detect-conflicts alone.
- apply without --from-file: one-shot propose+apply for trusted automation.

Report structure: {"domains": [...], "per_domain": {<d>: {<step>: ...}},
"cross_domain_conflicts": {...}} — the cross key exists only for 2+ domains.
"""
import json
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger

from artmind.conflicts import detect_conflicts
from artmind.consolidate import consolidate_descriptions
from artmind.graph_query import neo4j_session, normalize_domains
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


def _run_dir(domains: list[str], now: str) -> Path:
    d = REFINE_DIR / "pipeline" / "+".join(domains) / now.replace(":", "").replace("+", "_")
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
    domains: "str | list[str]",
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
    """Run the refine pipeline for one or more domains. See module docstring."""
    domains = normalize_domains(domains)
    env = load_env()
    resolved_model = resolve_llm_model(env, model)
    selected = resolve_steps(steps)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    mode = "apply" if (apply or from_file) else "propose"

    prior: dict = {}
    if from_file:
        prior = json.loads(Path(from_file).read_text(encoding="utf-8"))
        if set(prior.get("domains", [])) != set(domains):
            raise ValueError(
                f"--from-file report covers domains {prior.get('domains')!r}, "
                f"not {domains!r}"
            )

    run_dir = _run_dir(domains, now)
    report: dict = {
        "domains": domains,
        "command": "refine_pipeline",
        "mode": mode,
        "model": resolved_model,
        "started_at": now,
        "per_domain": {d: {} for d in domains},
    }

    def _prior_step(domain: str, step: str) -> dict:
        return prior.get("per_domain", {}).get(domain, {}).get(step, {})

    # ── 1. time + 2. supersession: deterministic, additive, idempotent ────────
    for d in domains:
        if "time" in selected:
            r = normalize_time(d, dry_run=False)
            report["per_domain"][d]["time"] = r
            logger.info("pipeline[{}] time: {}", d, r)
        if "supersession" in selected:
            r = detect_supersession(d, dry_run=False)
            report["per_domain"][d]["supersession"] = r
            logger.info("pipeline[{}] supersession: {}", d, r)

    # ── 3. merge: every domain merges before any cross-domain conflict pass ───
    applied_canonicals: dict[str, list[str]] = {}
    if "merge" in selected:
        for d in domains:
            if from_file:
                merges_file = _prior_step(d, "merge").get("proposals_file")
                if merges_file and Path(merges_file).exists():
                    merge_report = refine_graph(
                        domain=d,
                        name_filter=None,
                        model=resolved_model,
                        similarity_threshold=merge_threshold,
                        dry_run=False,
                        output_file=None,
                        from_file=Path(merges_file),
                    )
                    applied_canonicals[d] = sorted(
                        set(merge_report.get("proposed_merges", {}).values())
                    )
                else:
                    merge_report = {"skipped": "no proposals_file in report"}
            else:
                merges_file = run_dir / f"merges_{d}.json"
                merge_report = refine_graph(
                    domain=d,
                    name_filter=None,
                    model=resolved_model,
                    similarity_threshold=merge_threshold,
                    dry_run=(mode == "propose"),
                    output_file=merges_file,
                    from_file=None,
                )
                merge_report["proposals_file"] = str(merges_file)
                if mode == "apply":
                    applied_canonicals[d] = sorted(
                        set(merge_report.get("proposed_merges", {}).values())
                    )
            report["per_domain"][d]["merge"] = merge_report

    # ── 4. conflicts: intra-domain per domain, then one cross-domain pass ─────
    if "conflicts" in selected:
        for d in domains:
            if from_file:
                conflicts_file = _prior_step(d, "conflicts").get("proposals_file")
                if conflicts_file and Path(conflicts_file).exists():
                    conflict_report = detect_conflicts(
                        domains=[d], model=resolved_model, from_file=Path(conflicts_file)
                    )
                else:
                    conflict_report = {"skipped": "no proposals_file in report"}
            else:
                conflicts_file = run_dir / f"conflicts_{d}.json"
                conflict_report = detect_conflicts(
                    domains=[d],
                    sim_threshold=conflict_sim_threshold,
                    max_pairs=max_pairs,
                    model=resolved_model,
                    dry_run=(mode == "propose"),
                    output_file=conflicts_file,
                )
                conflict_report["proposals_file"] = str(conflicts_file)
            report["per_domain"][d]["conflicts"] = conflict_report

        if len(domains) >= 2:
            if from_file:
                cross_file = prior.get("cross_domain_conflicts", {}).get("proposals_file")
                if cross_file and Path(cross_file).exists():
                    cross_report = detect_conflicts(
                        domains=domains, model=resolved_model, from_file=Path(cross_file)
                    )
                else:
                    cross_report = {"skipped": "no proposals_file in report"}
            else:
                cross_file = run_dir / "conflicts_cross.json"
                cross_report = detect_conflicts(
                    domains=domains,
                    sim_threshold=conflict_sim_threshold,
                    max_pairs=max_pairs,
                    model=resolved_model,
                    dry_run=(mode == "propose"),
                    output_file=cross_file,
                )
                cross_report["proposals_file"] = str(cross_file)
            report["cross_domain_conflicts"] = cross_report

    # ── 5. consolidate ─────────────────────────────────────────────────────────
    # Propose shows a bounded sample per domain (LLM cost control); apply runs
    # live — consolidation is chunk-set-idempotent and conflict-gated, so
    # proposals don't need per-entity vetting the way merges do.
    if "consolidate" in selected:
        for d in domains:
            consolidate_report = consolidate_descriptions(
                domain=d,
                limit=(sample_consolidations if mode == "propose" else consolidate_limit),
                model=model,
                dry_run=(mode == "propose"),
            )
            counts = consolidate_report.get("counts", {})
            consolidate_report["candidates_total"] = counts.get("consolidate", 0) + counts.get(
                "skipped_over_limit", 0
            )
            report["per_domain"][d]["consolidate"] = consolidate_report

    # ── 6. embed sweep ─────────────────────────────────────────────────────────
    if "embed" in selected and mode == "apply":
        for d in domains:
            nulled = _null_embeddings_for_canonicals(d, applied_canonicals.get(d, []))
            try:
                embedded = embed_entities_backfill(d)["entities_embedded"]
            except Exception as exc:
                logger.warning("pipeline[{}] embed backfill failed: {}", d, exc)
                embedded = 0
            report["per_domain"][d]["embed"] = {
                "canonicals_nulled": nulled,
                "entities_embedded": embedded,
            }

    report_file = run_dir / "pipeline_report.json"
    report_file.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    report["report_file"] = str(report_file)
    if mode == "propose":
        domain_flags = " ".join(f"--domain {d}" for d in domains)
        report["apply_with"] = (
            f"uv run artmind ingest refine-pipeline {domain_flags} "
            f"--from-file {report_file}"
        )
    return report
