"""Mart review queue operations (accept, reject, set) shared by `artmind mart review …` and the
admin console's Curate → Mart pane, so a long queue can be worked in batches."""
from __future__ import annotations

from artmind.mart import llm, records


def vocabulary(decl) -> dict:
    """Levels per dimension and the closed value lists the UI offers (free text elsewhere)."""
    families = sorted((getattr(getattr(decl, "type", None), "families", None) or {}).keys())
    return {
        "levels": llm.LEVELS,
        "values": {
            "person": {"group": sorted(llm.GROUPS), "side": sorted(s for s in llm.SIDES if s),
                       "relation": sorted(llm.RELATIONS)},
            "type": {"family": families},
        },
    }


def check_levels(decl, dimension: str, levels: dict) -> dict:
    """Keep non-empty values; refuse a level the dimension lacks or a value outside a closed list."""
    if dimension not in llm.LEVELS:
        raise ValueError(f"unknown dimension {dimension!r}")
    closed = vocabulary(decl)["values"].get(dimension, {})
    out = {}
    for k, v in (levels or {}).items():
        v = str(v if v is not None else "").strip()
        if not v:
            continue
        if k not in llm.LEVELS[dimension]:
            raise ValueError(f"{dimension} has no level {k!r} (levels: {', '.join(llm.LEVELS[dimension])})")
        if k in closed and closed[k] and v not in closed[k]:
            raise ValueError(f"{dimension}.{k} must be one of: {', '.join(closed[k])}")
        out[k] = v
    if not out:
        raise ValueError("no level value given")
    return out


def plan(decl, ops: list[dict]) -> list[dict]:
    """Validate every op against the open queue before anything is written.
    An op is {action: accept|reject, id, levels?}: accept writes a user record with the given
    levels (default: the proposal's), reject marks the proposal rejected."""
    planned = []
    for op in ops:
        action = op.get("action")
        if action not in ("accept", "reject"):
            raise ValueError(f"unknown action {action!r}")
        rec = records.proposal(str(op.get("id") or ""))
        levels = None
        if action == "accept":
            levels = check_levels(decl, rec["dimension"], op.get("levels") or rec.get("levels") or {})
        planned.append({"action": action, "id": rec["id"], "dimension": rec["dimension"],
                        "member_key": rec["member_key"], "levels": levels})
    return planned


def apply(decl, planned: list[dict]) -> list[dict]:
    done = []
    for p in planned:
        if p["action"] == "accept":
            rid = records.put_dimension(decl.domain, p["dimension"], p["member_key"], p["levels"],
                                        source="user", confidence=1.0)
            done.append({**p, "record": rid})
        else:
            records.reject_proposal(p["id"])
            done.append({**p, "record": p["id"]})
    return done


def summary(planned: list[dict]) -> str:
    acc = sum(p["action"] == "accept" for p in planned)
    rej = len(planned) - acc
    parts = ([f"accept {acc} as user record(s)"] if acc else []) + ([f"reject {rej}"] if rej else [])
    return (" and ".join(parts) or "nothing") + "; run `mart build` to see it in the tables."
