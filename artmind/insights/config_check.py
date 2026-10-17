"""`artmind insights config check`: validate domains/insights/<domain>.yaml against the live graph.

Errors: invalid YAML or keys; configured classes or properties that are not in
the graph; anchors that resolve to nothing. Warnings: configured values that
never appear in the graph (a typo or a vocabulary drift).
"""

from __future__ import annotations

from artmind.graph_query import expand_domain_family
from artmind.insights.config import load_config
from artmind.insights.model import InsightsError
from artmind.insights.profile import build_profile, infer_anchors
from artmind.insights.projections import Projector
from artmind.temporal import load_schema


def _seen_values(projector, entities, profile, cls: str, prop: str) -> set[str] | None:
    if prop == "type":
        return {e.type.strip().casefold() for e in entities if e.cls == cls and e.type}
    if prop not in profile.classes[cls].properties:
        return None
    return {str(r["value"]).strip().casefold() for r in projector.rows("entity_property", cls=cls, prop=prop)
            if r.get("value") is not None}


def check_config(domain: str, projector=None) -> dict:
    errors: list[str] = []
    warnings: list[str] = []
    try:
        config = load_config(domain)
    except InsightsError as exc:
        return {"domain": domain, "ok": False, "errors": [str(exc)], "warnings": []}
    projector = projector or Projector(expand_domain_family(domain))
    profile, entities = build_profile(projector, load_schema(domain), config)
    classes = profile.classes

    def check_values(label: str, cls: str, prop: str, values: list[str]) -> None:
        if cls not in classes:
            errors.append(f"{label}: class {cls} is not in the graph")
            return
        seen = _seen_values(projector, entities, profile, cls, prop)
        if seen is None:
            errors.append(f"{label}: property {cls}.{prop} is not in the graph")
            return
        for v in values:
            if v not in seen:
                warnings.append(f"{label}: value {v!r} never appears in {cls}.{prop}")

    outcome = config.block("outcome")
    if outcome:
        check_values("outcome", outcome["class"], outcome["property"], outcome["positive"] + outcome["negative"])
    loops = config.block("open_loops")
    if loops:
        check_values("open_loops", loops["class"], loops["property"], loops["open"] + loops["closed"])
    for cls in config.detector("communities").get("classes") or []:
        if cls not in classes:
            errors.append(f"detectors.communities.classes: {cls} is not in the graph")
    for cls in (config.block("transitions") or {}).get("classes", []):
        if cls not in classes:
            errors.append(f"transitions.classes: {cls} is not in the graph")
    try:
        infer_anchors(entities, config, profile.dated_days)
    except InsightsError as exc:
        errors.append(f"anchors: {exc}")
    return {"domain": domain, "ok": not errors, "errors": errors, "warnings": warnings}
