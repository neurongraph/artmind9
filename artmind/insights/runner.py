"""The insights pipeline (spec §5): profile -> plan -> project -> compute -> score -> diff -> snapshot.

Narration (spec §11) is phase 3. `refresh` short-circuits when the
fingerprint is unchanged, unless forced or limited to `only` detectors.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import replace
from datetime import datetime, timezone

import networkx as nx
from neo4j.exceptions import DriverError, Neo4jError

from artmind.graph_query import expand_domain_family
from artmind.insights import store
from artmind.insights.config import config_hash, load_config
from artmind.insights.detectors import RunContext, Skip, registry
from artmind.insights.diff import apply_diff
from artmind.insights.model import Finding, InsightsError, PlanEntry, Run
from artmind.insights.profile import build_profile, infer_anchors
from artmind.insights.projections import Projector
from artmind.temporal import load_schema


def _hashes(domain: str) -> dict:
    from artmind import projection, same_as

    return {"schema": projection.schema_set_hash(), "same_as": same_as.content_hash(), "config": config_hash(domain)}


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def run_detectors(ctx: RunContext, selected: set[str] | None) -> tuple[list[Finding], list[PlanEntry]]:
    findings: list[Finding] = []
    plan: list[PlanEntry] = []
    for det in registry():
        if selected is not None and det.name not in selected:
            continue
        if not ctx.config.enabled(det.name):
            plan.append(PlanEntry(det.name, "skipped", reason="disabled in config"))
            continue
        targets = det.plan(ctx)
        if isinstance(targets, Skip):
            plan.append(PlanEntry(det.name, "skipped", reason=targets.reason))
            continue
        for target in targets:
            try:
                found = det.run(ctx, target)
            except (InsightsError, nx.NetworkXError) as exc:
                plan.append(PlanEntry(det.name, "failed", target=target, reason=str(exc)))
                continue
            plan.append(PlanEntry(det.name, "ran", target=target, findings=len(found)))
            findings.extend(found)
    return findings, plan


def _trim(findings: list[Finding], max_per_detector: int) -> list[Finding]:
    order = [d.name for d in registry()]
    by_detector: dict[str, list[Finding]] = {}
    for f in findings:
        by_detector.setdefault(f.detector, []).append(f)
    out = []
    for name in order:
        ranked = sorted(by_detector.get(name, []), key=lambda f: (-f.score, f.key))
        out.extend(ranked[:max_per_detector])
    return out


def refresh(domain: str, *, force: bool = False, only: list[str] | None = None,
            projector=None, now: str | None = None) -> dict:
    config = load_config(domain)
    selected = set(only) if only else None
    if selected:
        unknown = selected - {d.name for d in registry()}
        if unknown:
            raise InsightsError(f"unknown detector(s): {sorted(unknown)}")
    schema = load_schema(domain)
    try:
        projector = projector or Projector(expand_domain_family(domain))
        profile, entities = build_profile(projector, schema, config)
    except (Neo4jError, DriverError) as exc:
        raise InsightsError(f"graph unavailable: {getattr(exc, 'message', None) or exc}") from exc
    if not entities:
        raise InsightsError(f"domain {domain!r} has no entities in the graph")
    anchors = infer_anchors(entities, config, profile.dated_days)
    fingerprint = {**_hashes(domain), "entities": len(entities), "latest_doc": profile.latest_doc}

    previous = store.load_latest(domain)
    if previous and previous.fingerprint == fingerprint and not force and selected is None:
        return {"status": "up_to_date", "domain": domain, "created_at": previous.created_at, "as_of": previous.as_of}

    ctx = RunContext(domain=domain, schema=schema, config=config, profile=profile, entities=entities,
                     anchors={a["_id"] for a in anchors}, as_of=profile.latest_doc)
    found, plan = run_detectors(ctx, selected)
    found = _trim(found, config.max_findings)

    prev_findings, carried = None, []
    if previous is not None:
        prev_findings = [f for f in previous.findings if selected is None or f.detector in selected]
        if selected is not None:
            carried = [replace(f, diff="same") for f in previous.findings
                       if f.detector not in selected and f.diff != "gone"]
    runs = store.list_runs(domain)
    findings, diff = apply_diff(found, prev_findings, runs[-1].name if previous is not None and runs else None)

    circles = [c for key, (_g, cs) in sorted(ctx.memo.items(), key=lambda kv: str(kv[0]))
               if isinstance(key, tuple) and key[0] == "circles" for c in cs]
    if selected is not None and "communities" not in selected and previous is not None:
        circles = previous.circles

    run = Run(domain=domain, created_at=now or _now(), as_of=profile.latest_doc, fingerprint=fingerprint,
              anchors=anchors, profile=profile.to_dict(), plan=plan, findings=findings + carried,
              circles=circles, diff=diff)
    path = store.write_run(run)
    pruned = store.prune(domain, config.keep_runs)
    return {
        "status": "written", "domain": domain, "path": str(path), "as_of": run.as_of, "anchors": anchors,
        "findings": dict(sorted(Counter(f.detector for f in run.findings if f.diff != "gone").items())),
        "diff": diff, "skipped": [p.to_dict() for p in plan if p.status != "ran"], "pruned": pruned,
    }
