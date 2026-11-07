"""The evidence bundle (P3 spec §6): rows from every step, ranked by RRF, assembled to a budget.

Scores from different commands cannot be compared, so evidence is ranked by reciprocal-rank fusion
(k = 60) over each successful step's own order. Duplicates (same passage id, else chunk id) merge and
list every step they came from. The printed bundle holds the top 10 evidence rows, then one per
further document, then documents, facts and guides while they fit. The full bundle is stored under a
handle whenever anything was left out (`query page <handle> --list evidence|documents|facts|guides`).
"""

from __future__ import annotations

import json
import os

from artmind.query_run import adapters
from artmind.query_run import facts as facts_mod

BUNDLE_VERSION = 1
RRF_K = 60
EVIDENCE_TOP = 10
DEFAULT_BUDGET = 24_000
CMD_CHARS = 300
_RESERVE = 400   # truncated / rows_total / handle / next / a warning line
_LISTS = ("evidence", "documents", "facts", "guides")


def bundle_budget() -> int:
    raw = os.environ.get("ARTMIND_BUNDLE_BUDGET")
    if raw:
        try:
            value = int(raw)
        except ValueError:
            value = 0
        if value <= 0:
            raise ValueError(f"ARTMIND_BUNDLE_BUDGET must be a positive number of characters, got {raw!r}")
        return value
    from artmind import result_store

    try:
        return result_store.current_budget() or DEFAULT_BUDGET
    except result_store.BudgetError as exc:
        raise ValueError(str(exc)) from None


def _dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))


def _key(row: dict) -> str:
    return str(row.get("passage_id") or row.get("chunk_id") or row.get("text", "")[:80])


def _rank(ex) -> tuple[list, list, list, list]:
    ev: dict[str, dict] = {}
    ev_score: dict[str, float] = {}
    docs: dict[str, dict] = {}
    doc_score: dict[str, float] = {}
    facts: list = []
    guides: list = []
    for sid in ex.answer_ids or ex.results:
        result = ex.results.get(sid)
        if result is None or result.exit_code != 0 or result.payload is None:
            continue
        rows = adapters.adapt(result.payload)
        for r, row in enumerate(rows.evidence, 1):
            k = _key(row)
            if k not in ev:
                ev[k] = {**row, "from": [], "clause": None}
            if sid not in ev[k]["from"]:
                ev[k]["from"].append(sid)
            ev_score[k] = ev_score.get(k, 0.0) + 1.0 / (RRF_K + r)
            doc = row.get("doc") or {}
            if doc.get("id"):
                docs.setdefault(doc["id"], {"doc_id": doc["id"], "title": doc.get("title"), "domain": doc.get("domain"),
                                            "date": doc.get("date"), "status": doc.get("status"), "gist": None, "gist_source": None})
                doc_score[doc["id"]] = doc_score.get(doc["id"], 0.0) + 1.0 / (RRF_K + r)
        for r, d in enumerate(rows.documents, 1):
            if not d.get("doc_id"):
                continue
            base = docs.setdefault(d["doc_id"], dict(d))
            for field in ("gist", "gist_source", "title", "date", "status", "domain"):
                if base.get(field) is None and d.get(field) is not None:
                    base[field] = d[field]
            doc_score[d["doc_id"]] = doc_score.get(d["doc_id"], 0.0) + 1.0 / (RRF_K + r)
        facts += [{**f, "from": sid} for f in rows.facts]
        guides += rows.guides
    evidence = [ev[k] for k in sorted(ev, key=lambda k: -ev_score[k])]
    for i, row in enumerate(evidence, 1):
        row["eid"], row["rank"] = f"e{i}", i
    documents = [docs[k] for k in sorted(docs, key=lambda k: -doc_score[k])]
    by_doc: dict[str, list] = {}
    for row in evidence:
        by_doc.setdefault((row.get("doc") or {}).get("id"), []).append(row["eid"])
    for i, d in enumerate(documents, 1):
        d["rank"], d["evidence"] = i, by_doc.get(d["doc_id"], [])
    for i, f in enumerate(facts, 1):
        f["fid"] = f"f{i}"
    return evidence, documents, facts, guides


def _selected_evidence(evidence: list) -> list:
    out, seen = list(evidence[:EVIDENCE_TOP]), {(e.get("doc") or {}).get("id") for e in evidence[:EVIDENCE_TOP]}
    for row in evidence[EVIDENCE_TOP:]:
        doc = (row.get("doc") or {}).get("id")
        if doc not in seen:
            out.append(row)
            seen.add(doc)
    return out


def _coverage(ex, evidence) -> dict:
    ran = [(sid, r) for sid, r in ex.results.items() if r.exit_code == 0]
    read = set().union(*(set(ex.facts.get(sid, {}).get("_docs") or ()) for sid, _ in ran)) if ran else set()
    depths = [facts_mod._option_int(r.argv, ("--topK", "--limit")) for _, r in ran]
    return {"documents_read": len(read), "documents_total": None,
            "found": len({(e.get("doc") or {}).get("id") for e in evidence if (e.get("doc") or {}).get("id")}),
            "exhaustive": not any(ex.facts.get(sid, {}).get("truncated") for sid, _ in ran),
            "partial": ex.partial, "probes": [], "depth": max([d for d in depths if d] or [0]) or None}


def _plan_block(plan: dict) -> dict:
    conf = plan.get("confidence") or {}
    return {"intent": (plan.get("intent") or {}).get("id"), "route": (plan.get("route") or {}).get("id"),
            "program_kind": plan.get("program_kind"),
            "confidence": {k: conf.get(k) for k in ("level", "score", "threshold", "weakest")},
            "classifier": (plan.get("classifier") or {}).get("backend"), "subplans": []}


def _path(ex) -> list:
    return [{**p, "cmd": p["cmd"][:CMD_CHARS]} if isinstance(p.get("cmd"), str) else p for p in ex.path]


def build(plan: dict, ex, *, budget: int, plan_ms: float | None = None) -> tuple[dict, dict]:
    """(printed bundle, full bundle). The printed one fits `budget` characters as compact JSON."""
    evidence, documents, facts, guides = _rank(ex)
    base = {"command": "query run", "warnings": [], "next": None, "bundle_version": BUNDLE_VERSION, "mode": "reasoning",
            "plan": _plan_block(plan), "scope": plan.get("scope"), "path": _path(ex),
            "coverage": _coverage(ex, evidence), "gaps": plan.get("gaps") or [], "escalate": ex.escalate, "offer": None,
            "timing": {"plan_ms": plan_ms, "exec_ms": ex.exec_ms}}
    trimmed: list[str] = []
    if len(_dumps(base)) + _RESERVE > budget:  # the fixed part alone is over: shrink it, never the evidence first
        base["path"] = [{k: v for k, v in p.items() if k != "facts"} for p in base["path"]]
        trimmed.append("step facts left out of path")
        if len(_dumps(base)) + _RESERVE > budget and isinstance(base["escalate"], dict):
            sug = base["escalate"].get("suggested")
            if isinstance(sug, list) and len(sug) > 3:
                base["escalate"] = {**base["escalate"], "suggested": sug[:3]}
                trimmed.append("suggested steps cut to 3")
    full = {**base, "evidence": evidence, "documents": documents, "facts": facts, "guides": guides}
    printed = {**base, "evidence": [], "documents": [], "facts": [], "guides": []}
    candidates = {"evidence": _selected_evidence(evidence), "documents": documents, "facts": facts, "guides": guides}
    size = len(_dumps(printed)) + _RESERVE
    cut = len(candidates["evidence"]) < len(evidence)
    for name in _LISTS:
        for row in candidates[name]:
            n = len(_dumps(row)) + 1
            if size + n > budget:
                cut = True
                break
            printed[name].append(row)
            size += n
        else:
            continue
        break
    for name in _LISTS:
        if len(printed[name]) < len(full[name]):
            cut = True
    printed.update(truncated=False, rows_total=len(evidence), handle=None)
    if trimmed:
        printed["warnings"] = [f"bundle budget {budget:,} characters: " + "; ".join(trimmed)]
    if cut:
        import paths
        from artmind import result_store

        handle = result_store.store(paths.LOGS_DIR / "results", full, _dumps)
        shown = len(printed["evidence"])
        printed.update(truncated=True, handle=handle, next=f"artmind query page {handle} --list evidence --from {shown}")
        printed["warnings"] = [*printed["warnings"], f"bundle budget {budget:,} characters: showing {shown} of {len(evidence)} evidence rows, "
                               f"{len(printed['documents'])} of {len(documents)} documents, "
                               f"{len(printed['facts'])} of {len(facts)} facts"]
    return printed, full
