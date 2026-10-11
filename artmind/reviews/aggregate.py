"""Shape findings across dossiers, by schema element (spec 2026-10-11 §7.5).
One systemic problem should be one dossier listing every symptom; this is
where the reviewer sees that an element already has one."""

from collections import Counter

from artmind.reviews.dossier import Dossier, iter_dossiers

ACTIVE = ("open", "in-progress")


def findings(dossier: Dossier) -> list[dict]:
    """Shape-audit table rows as {element, check}, then frontmatter elements
    the table does not mention (check None)."""
    out: list[dict] = []
    for line in (dossier.sections.get("Shape audit") or "").split("\n"):
        row = line.strip()
        if not row.startswith("|") or set(row) <= set("|-: "):
            continue
        cells = [c.strip().strip("`").strip() for c in row.strip("|").split("|")]
        if len(cells) < 2 or cells[0].lower() == "element" or not cells[0]:
            continue
        out.append({"element": cells[0], "check": cells[1].lower() or None})
    seen = {f["element"] for f in out}
    out += [{"element": str(e), "check": None} for e in dossier.meta.get("elements") or [] if str(e) not in seen]
    return out


def shape_report(*, domain: str | None = None, statuses=ACTIVE) -> list[dict]:
    groups: dict[str, dict] = {}
    for _, dossier, error in iter_dossiers():
        if error or dossier.meta.get("status") not in statuses:
            continue
        for finding in findings(dossier):
            element = finding["element"]
            if domain and not element.startswith(f"{domain}/"):
                continue
            group = groups.setdefault(element, {"dossiers": {}, "checks": Counter(), "trace_ids": set()})
            group["dossiers"][dossier.review_id] = {"review_id": dossier.review_id,
                                                     "status": dossier.meta.get("status"),
                                                     "stage": dossier.meta.get("stage")}
            if finding["check"]:
                group["checks"][finding["check"]] += 1
            group["trace_ids"].update(dossier.meta.get("trace_ids") or [])
    rows = [{"element": element, "traces": len(g["trace_ids"]), "dossiers": list(g["dossiers"].values()),
             "checks": dict(g["checks"])} for element, g in groups.items()]
    rows.sort(key=lambda r: (-r["traces"], -len(r["dossiers"]), r["element"]))
    return rows
