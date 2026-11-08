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
import shlex

from artmind.query_run import adapters, opening
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
        if sid == opening.OPEN_ID:
            continue
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


def _apply_open(ex, evidence: list, documents: list) -> tuple[list, list, list]:
    """Spec 6.5: opened passages first (`from: ["open"]`), `opened` flags on documents, the `opened` list.

    Returns (evidence, opened rows, opened list). Evidence ids are renumbered, opened rows first. A failed
    open step changes nothing.
    """
    for d in documents:
        d["opened"] = False
    result = ex.results.get(opening.OPEN_ID)
    if result is None:
        return evidence, [], []
    if result.exit_code != 0 or result.error or result.payload is None:
        return evidence, [], []
    rows = adapters.adapt(result.payload).evidence
    merged: dict[str, dict] = {}
    for row in rows:
        k = _key(row)
        merged.setdefault(k, {**row, "from": ["open"], "clause": None})
    rest = []
    for row in evidence:
        k = _key(row)
        if k in merged:
            merged[k]["from"] = ["open", *[f for f in row["from"] if f != "open"]]
        else:
            rest.append(row)
    opened_rows = list(merged.values())
    evidence = [*opened_rows, *rest]
    for i, row in enumerate(evidence, 1):
        row["eid"], row["rank"] = f"e{i}", i
    by_doc: dict[str, list] = {}
    for row in evidence:
        by_doc.setdefault((row.get("doc") or {}).get("id"), []).append(row["eid"])
    titles = {d["doc_id"]: d.get("title") for d in documents}
    opened = []
    for item in ex.opened:
        eids = [r["eid"] for r in opened_rows if (r.get("doc") or {}).get("id") == item["doc_id"]]
        opened.append({"doc_id": item["doc_id"], "title": titles.get(item["doc_id"]) or next(
            ((r.get("doc") or {}).get("title") for r in opened_rows if (r.get("doc") or {}).get("id") == item["doc_id"]), None),
            "why": item["why"], "passages": eids})
    got = {o["doc_id"] for o in opened if o["passages"]}
    for d in documents:
        d["opened"] = d["doc_id"] in got
        d["evidence"] = by_doc.get(d["doc_id"], [])
    return evidence, opened_rows, opened


def _line(d: dict) -> dict:
    """The short document row of the printed bundle (spec 6.5): about 80 characters."""
    return {"doc_id": d["doc_id"], "title": d.get("title"), "date": d.get("date"),
            "opened": bool(d.get("opened")), "rank": d.get("rank")}


def _open_next(plan: dict, documents: list) -> dict | None:
    """The command that opens the next unopened documents by rank (spec 6.5), or None."""
    from artmind.query_run import executor

    ids = opening.next_unopened(documents)
    domains = executor._plan_domains(plan)
    if not ids or not domains:
        return None
    question = plan.get("effective_question") or plan.get("question") or ""
    argv = opening.command(ids, domains, question)
    return {"doc_ids": ids, "command": "artmind " + " ".join(shlex.quote(a) for a in argv)}


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
    evidence, opened_rows, opened = _apply_open(ex, evidence, documents)
    open_result = ex.results.get(opening.OPEN_ID)
    open_failed = open_result is not None and (open_result.exit_code != 0 or bool(open_result.error) or open_result.payload is None)
    base = {"command": "query run", "warnings": [], "next": None, "bundle_version": BUNDLE_VERSION, "mode": "reasoning",
            "plan": _plan_block(plan), "scope": plan.get("scope"), "path": _path(ex),
            "coverage": _coverage(ex, evidence), "gaps": plan.get("gaps") or [], "escalate": ex.escalate, "offer": None,
            "timing": {"plan_ms": plan_ms, "exec_ms": ex.exec_ms}, "opened": opened}
    trimmed: list[str] = []
    extra_warnings: list[str] = []
    if open_failed:
        extra_warnings.append("opening documents failed; the bundle lists documents it did not read "
                              f"(open step: {open_result.error or 'exit ' + str(open_result.exit_code)})")
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
    printed["open_next"] = _open_next(plan, documents)  # never cut: its size is counted before anything is placed
    lines = [_line(d) for d in documents]
    candidates = {"evidence": _selected_evidence(evidence[len(opened_rows):]), "documents": lines,
                  "facts": facts, "guides": guides}
    size = len(_dumps(printed)) + _RESERVE
    for row in opened_rows:  # opened passages are placed first and never cut
        printed["evidence"].append(row)
        size += len(_dumps(row)) + 1
    if size > budget:
        extra_warnings.append(f"opened passages alone ({len(opened_rows)} rows) exceed the bundle budget "
                              f"{budget:,} characters; they are kept and nothing else is placed")
    stop = False
    for name in _LISTS:  # evidence, document line rows, facts, guides: once one list is cut the rest are not started
        for row in candidates[name]:
            n = len(_dumps(row)) + 1
            if stop or size + n > budget:
                stop = True
                break
            printed[name].append(row)
            size += n
    gisted = 0
    with_gist = [d for d in documents if not d.get("opened") and d.get("gist")]
    if not stop and len(printed["documents"]) == len(documents):  # gists last, in rank order, while budget remains
        by_id = {d["doc_id"]: d for d in printed["documents"]}
        for d in with_gist:
            extra = len(_dumps({"gist": d["gist"], "gist_source": d.get("gist_source")})) + 1
            if size + extra > budget:
                break
            by_id[d["doc_id"]].update(gist=d["gist"], gist_source=d.get("gist_source"))
            size += extra
            gisted += 1
    cut = {"evidence": len(evidence) - len(printed["evidence"]), "documents": len(documents) - len(printed["documents"]),
           "facts": len(facts) - len(printed["facts"]), "guides": len(guides) - len(printed["guides"]),
           "gists": len(with_gist) - gisted}
    printed.update(truncated=False, rows_total=len(evidence), handle=None, cut=cut)
    printed["warnings"] = list(extra_warnings)
    if trimmed:
        printed["warnings"].append(f"bundle budget {budget:,} characters: " + "; ".join(trimmed))
    if any(cut.values()):
        import paths
        from artmind import result_store

        handle = result_store.store(paths.LOGS_DIR / "results", full, _dumps)
        first = next((n for n in _LISTS if cut[n]), None)  # gists only: no list lost a row, so paging adds nothing
        printed.update(truncated=True, handle=handle,
                       next=f"artmind query page {handle} --list {first} --from {len(printed[first])}" if first else None)
        totals = {"evidence": len(evidence), "documents": len(documents), "facts": len(facts), "guides": len(guides),
                  "gists": len(with_gist)}
        left = [f"{cut[n]} of {totals[n]} {n if n != 'gists' else 'document gists'}" for n in (*_LISTS, "gists") if cut[n]]
        printed["warnings"] = [*printed["warnings"], f"bundle budget {budget:,} characters: left out " + ", ".join(left)]
    return printed, full
