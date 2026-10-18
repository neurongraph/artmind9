"""Reading a snapshot for people and agents: shared by `artmind insights list/show` and `query insights`."""

from __future__ import annotations

LIVE = frozenset({"new", "changed", "same"})
CHANGED = frozenset({"new", "changed", "gone"})


def list_findings(run, *, detector: str | None = None, kind: str | None = None,
                  only_new: bool = False, limit: int | None = None) -> list:
    wanted = CHANGED if only_new else LIVE
    items = [f for f in run.findings
             if f.diff in wanted and (detector is None or f.detector == detector) and (kind is None or f.kind == kind)]
    items.sort(key=lambda f: (-f.score, f.key))
    return items[:limit] if limit else items


def finding_summary(f) -> dict:
    return {"key": f.key, "detector": f.detector, "kind": f.kind, "score": f.score, "title": f.title,
            "narration": f.narration, "diff": f.diff, "action": f.action,
            "subjects": [{"_id": s.get("_id"), "name": s.get("name"), "class": s.get("class")} for s in f.subjects]}


def circle_summary(c: dict) -> dict:
    return {"id": c.get("id"), "name": c.get("name"), "name_source": c.get("name_source"),
            "size": len(c.get("members", []))}
