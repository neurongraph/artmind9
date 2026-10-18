"""The insights pipeline (spec §5): profile -> plan -> project -> compute -> score -> diff -> snapshot.

Narration (spec §11) is phase 3. `refresh` short-circuits when the
fingerprint is unchanged, unless forced or limited to `only` detectors.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from dataclasses import replace
from datetime import datetime, timezone

import networkx as nx
import yaml
from loguru import logger
from neo4j.exceptions import DriverError, Neo4jError

from artmind.graph_query import expand_domain_family
from artmind.insights import store
from artmind.insights.config import config_hash, load_config
from artmind.insights.detectors import RunContext, Skip, registry
from artmind.insights.diff import apply_diff
from artmind.insights.model import Finding, InsightsError, PlanEntry, Run
from artmind.insights.profile import build_profile, infer_anchors, iso_days
from artmind.insights.projections import Projector
from artmind.temporal import load_schema


def _hashes(domain: str) -> dict:
    from artmind import projection, same_as
    from artmind.insights.detectors.same_as import ALIASES_FILE

    return {"schema": projection.schema_set_hash(), "same_as": same_as.content_hash(), "config": config_hash(domain),
            "aliases": hashlib.sha256(ALIASES_FILE.read_bytes()).hexdigest()}


def _evidence_hash(entities) -> str:
    """Hash of every entity's evidence days and document count: a moved or
    re-extracted observation changes it even when counts and dates do not."""
    h = hashlib.sha256()
    for e in entities:  # build_profile sorts by _id
        h.update(f"{e._id}|{e.cls}|{e.docs}|{','.join(e.days)}\n".encode())
    return h.hexdigest()


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
            plan.append(PlanEntry(det.name, "skipped", reason=targets.reason, carry=targets.carry))
            continue
        for target in targets:
            try:
                found = det.run(ctx, target)
            except (InsightsError, nx.NetworkXError) as exc:
                plan.append(PlanEntry(det.name, "failed", target=target, reason=str(exc)))
                continue
            plan.append(PlanEntry(det.name, "ran", target=target, findings=len(found)))
            findings.extend(found)
            ctx.memo.setdefault("findings", []).extend(found)
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


# Bump when detector logic changes; config and aliases.yaml edits are already in the fingerprint.
DETECTORS_VERSION = 3
# A selected detector that reads another's output (brokers read communities' circles).
DEPENDS = {
    "brokers": {"communities"},
    "transitions": {"communities"},
    "split_signal": {"same_as_candidates", "salience_trend", "outcome_lift"},
}


def load_entity_props(projector, config, profile) -> dict:
    """Per-entity values for the properties the configured detectors read:
    - the outcome property (unless `type`);
    - the open_loops status and urgency;
    - relationship_to_* on recurrent classes, for same-as agreement.
    Only properties the profile has seen are loaded."""
    wanted: set[tuple[str, str]] = set()
    outcome = config.block("outcome")
    if outcome and outcome["property"] != "type":
        wanted.add((outcome["class"], outcome["property"]))
    loops = config.block("open_loops")
    if loops:
        wanted |= {(loops["class"], loops["property"]), (loops["class"], loops["urgency_property"])}
    for cls, cp in profile.classes.items():
        if cp.effective_kind == "recurrent":
            wanted |= {(cls, p) for p in cp.properties if p.startswith("relationship_to")}
    out = {}
    for cls, prop in sorted(wanted):
        if cls in profile.classes and prop in profile.classes[cls].properties:
            out[(cls, prop)] = {r["_id"]: r["value"] for r in projector.rows("entity_property", cls=cls, prop=prop)
                                if r.get("_id") and r.get("value") is not None}
    return out


def load_anchor_relations(projector, anchors: list[dict]) -> list[dict]:
    if not anchors:
        return []
    out = []
    for r in projector.rows("anchor_relations", anchors=sorted(a["_id"] for a in anchors)):
        if not r.get("rel") or not r.get("_id"):
            continue
        out.append({"rel": str(r["rel"]), "_id": r["_id"], "name": r.get("name") or r["_id"],
                    "cls": r.get("cls") or "UNKNOWN", "type": r.get("type") if isinstance(r.get("type"), str) else None,
                    "days": iso_days(r.get("days"))})
    return out


def _keeps_previous(plan: list[PlanEntry]) -> set[str]:
    """Detectors that could not run: all targets failed, or every entry skipped for a transient reason
    (carry=True, not config-disabled). Their previous findings are carried over rather than reported
    as gone; a conclusive skip (carry=False) or a disabled detector drops them."""
    by_det: dict[str, list[PlanEntry]] = {}
    for p in plan:
        by_det.setdefault(p.detector, []).append(p)
    keep = set()
    for name, entries in by_det.items():
        if all(e.status == "failed" for e in entries):
            keep.add(name)
        elif any(e.status == "skipped" for e in entries) and all(
                e.status == "skipped" and e.carry and e.reason != "disabled in config" for e in entries):
            keep.add(name)
    return keep


def refresh(domain: str, *, force: bool = False, only: list[str] | None = None,
            projector=None, now: str | None = None, narrate: bool | None = None, llm=None) -> dict:
    try:
        return _refresh(domain, force=force, only=only, projector=projector, now=now, narrate=narrate, llm=llm)
    except InsightsError:
        raise
    except (OSError, yaml.YAMLError, ValueError) as exc:
        raise InsightsError(f"insights refresh for {domain!r} failed: {exc}") from exc


def _refresh(domain: str, *, force: bool, only: list[str] | None, projector, now: str | None,
             narrate: bool | None = None, llm=None) -> dict:
    config = load_config(domain)
    do_narrate = config.narrate if narrate is None else narrate
    selected = set(only) if only else None
    if selected:
        unknown = selected - {d.name for d in registry()}
        if unknown:
            raise InsightsError(f"unknown detector(s): {sorted(unknown)}")
    run_set = None
    if selected is not None:
        run_set = set(selected)
        for name in selected:
            run_set |= DEPENDS.get(name, set())
    schema = load_schema(domain)
    try:
        projector = projector or Projector(expand_domain_family(domain))
        profile, entities = build_profile(projector, schema, config)
        anchors = infer_anchors(entities, config, profile.dated_days) if entities else []
        entity_props = load_entity_props(projector, config, profile)
        anchor_relations = load_anchor_relations(projector, anchors)
    except (Neo4jError, DriverError) as exc:
        raise InsightsError(f"graph unavailable: {getattr(exc, 'message', None) or exc}") from exc
    if not entities:
        raise InsightsError(f"domain {domain!r} has no entities in the graph")
    fingerprint = {
        **_hashes(domain), "entities": len(entities), "latest_doc": profile.latest_doc,
        "documents": profile.documents,
        "evidence": _evidence_hash(entities),
        "relations": sum(r["n"] for r in profile.relations),
        "detectors_version": DETECTORS_VERSION,
        "narrated": bool(do_narrate),
    }

    try:
        previous = store.load_latest(domain)
    except InsightsError:
        if not force:
            raise
        previous = None  # a corrupt latest.json is replaced by a forced refresh
    if previous and previous.fingerprint == fingerprint and not force and selected is None:
        return {"status": "up_to_date", "domain": domain, "created_at": previous.created_at, "as_of": previous.as_of}
    if selected is not None:
        fingerprint = {**fingerprint, "partial": sorted(selected)}  # never equals a full run's fingerprint

    ctx = RunContext(domain=domain, schema=schema, config=config, profile=profile, entities=entities,
                     anchors={a["_id"] for a in anchors}, as_of=profile.latest_doc,
                     entity_props=entity_props, anchor_relations=anchor_relations)
    found, plan = run_detectors(ctx, run_set)
    if selected is not None:  # dependencies ran only to feed the selected detectors
        found = [f for f in found if f.detector in selected]
    found = _trim(found, config.max_findings)

    keep = _keeps_previous([p for p in plan if selected is None or p.detector in selected])
    prev_findings, carried = None, []
    if previous is not None:
        prev_findings = [f for f in previous.findings
                         if (selected is None or f.detector in selected) and f.detector not in keep]
        carried = [replace(f, diff="same") for f in previous.findings
                   if f.diff != "gone" and ((selected is not None and f.detector not in selected) or f.detector in keep)]
    runs = store.list_runs(domain)
    findings, diff = apply_diff(found, prev_findings, runs[-1].name if previous is not None and runs else None)
    if previous is not None and carried:
        diff["same"] += len(carried)

    circles = [c for key, val in sorted(ctx.memo.items(), key=lambda kv: str(kv[0]))
               if isinstance(key, tuple) and key[0] == "circles" for c in val[1]]
    if previous is not None and ((selected is not None and "communities" not in selected) or "communities" in keep):
        circles = previous.circles

    llm_stats: dict = {}
    from artmind.insights import narrate as narrate_mod
    if do_narrate and llm is None:
        try:
            llm, model = narrate_mod.default_llm()
        except Exception as exc:  # no provider configured: run without narration
            logger.warning("insights narration unavailable: {}", exc)
            llm_stats = {"errors": 1, "error": type(exc).__name__}
    else:
        model = None
    prev_circles = previous.circles if previous is not None else None
    live = [f for f in findings + carried if f.diff != "gone"]
    if do_narrate and llm is not None:
        c_stats = narrate_mod.name_circles(circles, previous=prev_circles, ctx=ctx, llm=llm)
        narrate_mod.label_circle_findings(live, circles)
        f_stats = narrate_mod.narrate_findings(live, previous=previous.findings if previous else None,
                                               ctx=ctx, llm=llm, top=config.narrate_top)
        llm_stats = {"model": model, "prompt_version": narrate_mod.PROMPT_VERSION,
                     "calls": c_stats["calls"] + f_stats["calls"],
                     "narrated": f_stats["narrated"], "cached": f_stats["cached"],
                     "rejected": f_stats["rejected"] + c_stats["rejected"],
                     "errors": f_stats["errors"] + c_stats["errors"],
                     "skipped_batches": f_stats["skipped_batches"],
                     "circles_named": c_stats["named"], "circles_kept": c_stats["kept"]}
    else:  # narration off or unavailable: keep earlier captions and circle names
        narrate_mod.carry_cached(live, previous.findings if previous else None, circles, prev_circles, ctx=ctx)
    fingerprint["narrated"] = bool(do_narrate) and llm is not None and llm_stats.get("errors", 0) == 0

    run = Run(domain=domain, created_at=now or _now(), as_of=profile.latest_doc, fingerprint=fingerprint,
              anchors=anchors, profile=profile.to_dict(), plan=plan, findings=findings + carried,
              circles=circles, diff=diff, llm=llm_stats)
    path = store.write_run(run)
    pruned = store.prune(domain, config.keep_runs)
    return {
        "status": "written", "domain": domain, "path": str(path), "as_of": run.as_of, "anchors": anchors,
        "findings": dict(sorted(Counter(f.detector for f in run.findings if f.diff != "gone").items())),
        "diff": diff, "llm": llm_stats, "skipped": [p.to_dict() for p in plan if p.status != "ran"], "pruned": pruned,
    }
