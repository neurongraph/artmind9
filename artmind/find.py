"""`query find`: one part of a question, searched in every passage of a survey's documents.

Spec docs/superpowers/specs/2026-11-12-find-in-result-design.md. Read-only, no LLM: one
Cypher read of the passages in scope, one embedding of the part, ranked in Python.
"""
from __future__ import annotations

import math
import re

from artmind import brief

#: Passages returned / at most this many from one document (spec §1).
TOP = 8
PER_DOC = 3
MAX_TOP = 20
MAX_PER_DOC = 5
#: Vector : keyword weights, as in `vector-text` (retrieval_rank.LEG_WEIGHTS).
WEIGHTS = {"vector": 2.0, "keyword": 1.0}
RRF_K = 60

NOT_A_SURVEY = "find searches a survey's documents; run query survey --shape brief first and pass its handle"

# Facts a part names exactly: codes (COM-POL-006), money, percentages, ISO dates, versions,
# durations (8 weeks), numbers of three or more digits (a bare 8 would match inside 2018).
_FACT = re.compile(r"[A-Za-z]{2,}(?:-[A-Za-z0-9]+)+|[£$€]\s?\d[\d,]*(?:\.\d+)?|\d+(?:\.\d+)?\s?%"
                   r"|\b\d{4}-\d{2}-\d{2}\b|\bv\d+(?:\.\d+)+\b"
                   r"|\b\d+(?:\.\d+)?\s?(?:business days|days?|weeks?|months?|years?|hours?)\b"
                   r"|\b\d[\d,]{2,}(?:\.\d+)?\b")

_PASSAGES = """
MATCH (p:DocPassage) WHERE p.doc_id IN $docIds AND p._domain IN $domains
RETURN p.id AS id, p.chunk_id AS chunk_id, p.doc_id AS doc_id, p.text AS text, p.embedding AS embedding
UNION ALL
MATCH (p:DocPassageHistory) WHERE p.doc_id IN $docIds AND p._domain IN $domains
RETURN p.id AS id, p.chunk_id AS chunk_id, p.doc_id AS doc_id, p.text AS text, p.embedding AS embedding
"""


def _norm(text: str) -> str:
    return re.sub(r"\s+", "", str(text or "").lower()).replace(",", "")


def facts(part: str) -> list[str]:
    """The exact facts a part names, normalised for a substring match (spaces and commas dropped)."""
    out: list[str] = []
    for m in _FACT.findall(part):
        n = _norm(m)
        if n and n not in out:
            out.append(n)
    return out


def _cosine(a, b) -> float | None:
    if not a or not b or len(a) != len(b):
        return None
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else None


def rank(part: str, passages: list[dict], embedding: list[float] | None) -> list[dict]:
    """Passages ranked for `part`: weighted RRF of a vector leg and a keyword leg. In the keyword
    leg a passage holding more of the part's exact facts sorts first, then by idf-weighted terms.
    Each returned dict gains `relevance` (cosine or None), `matched` and `_score`."""
    terms = brief.question_terms(part)
    folded = [brief.fold(t) for t in terms]
    exact = facts(part)
    tokens = [brief._tokens(p.get("text") or "") for p in passages]
    flat = [_norm(p.get("text") or "") for p in passages]
    n = len(passages) or 1
    df = {t: sum(t in tk for tk in tokens) for t in folded}
    idf = {t: math.log(1 + n / (1 + df[t])) for t in folded}

    scored = []
    for p, tk, fl in zip(passages, tokens, flat):
        hit_terms = [t for t, f in zip(terms, folded) if f in tk]
        hit_facts = [x for x in exact if x in fl]
        kw = sum(idf[brief.fold(t)] for t in hit_terms)
        cos = _cosine(embedding, p.get("embedding")) if embedding is not None else None
        matched = list(dict.fromkeys([*[m for m in _FACT.findall(part) if _norm(m) in hit_facts], *hit_terms]))
        scored.append({**{k: v for k, v in p.items() if k != "embedding"},
                       "relevance": cos, "matched": matched,
                       "_kw": (len(hit_facts), kw), "_has_kw": bool(hit_terms or hit_facts)})

    score = {id(s): 0.0 for s in scored}
    vec = sorted((s for s in scored if s["relevance"] is not None), key=lambda s: -s["relevance"])
    for r, s in enumerate(vec, 1):
        score[id(s)] += WEIGHTS["vector"] / (RRF_K + r)
    kw = sorted((s for s in scored if s["_has_kw"]), key=lambda s: (-s["_kw"][0], -s["_kw"][1]))
    for r, s in enumerate(kw, 1):
        score[id(s)] += WEIGHTS["keyword"] / (RRF_K + r)
    for s in scored:
        s["_score"] = score[id(s)]
    # Exact facts first overall: a passage holding the part's code beats one only close by meaning.
    return sorted((s for s in scored if s["_score"] > 0),
                  key=lambda s: (-s["_kw"][0], -s["_score"], str(s.get("id"))))


def survey_scope(stored: dict) -> tuple[str, list[str], list[str]]:
    """(survey question, domains, doc ids) of a stored brief survey; ValueError when it isn't one."""
    if not isinstance(stored, dict) or stored.get("command") != "query survey" or stored.get("query_type") != "survey":
        raise ValueError(NOT_A_SURVEY)
    docs = [d.get("doc_id") for d in stored.get("documents") or [] if isinstance(d, dict) and d.get("doc_id")]
    domains = [d for d in stored.get("domains") or [] if d]
    if not docs:
        raise ValueError("the stored survey lists no documents; run a new survey")
    return str(stored.get("question") or ""), domains, docs


def find(stored: dict, handle: str, part: str, *, top: int = TOP, per_doc: int = PER_DOC,
         session_factory=None, embed=None) -> dict:
    from artmind import graph_query, vector_query
    from artmind.survey import _DOCUMENTS
    from artmind.graph_query import serialize_record

    if not 1 <= int(top) <= MAX_TOP:
        raise ValueError(f"--top must be between 1 and {MAX_TOP}")
    if not 1 <= int(per_doc) <= MAX_PER_DOC:
        raise ValueError(f"--perDoc must be between 1 and {MAX_PER_DOC}")
    if not part.strip():
        raise ValueError("give the part to look for")
    question, domains, doc_ids = survey_scope(stored)
    if not domains:
        raise ValueError("the stored survey names no domains; run a new survey")
    expanded = vector_query._expand(domains)
    warnings: list[str] = []
    embedding = None
    try:
        embedding = (embed or vector_query.embed_question)(part)
    except Exception as e:
        warnings.append(f"vector leg unavailable: {e}")
    with (session_factory or graph_query.read_session)() as session:
        # Not serialize_record: it drops `embedding`, which the vector leg needs.
        passages = [{k: r[k] for k in ("id", "chunk_id", "doc_id", "text", "embedding")}
                    for r in session.run(_PASSAGES, docIds=doc_ids, domains=expanded)]
        meta = {r["doc_id"]: r for r in (serialize_record(x) for x in session.run(_DOCUMENTS, docIds=doc_ids))}
    missing = sum(1 for p in passages if not p.get("embedding"))
    if missing and embedding is not None:
        warnings.append(f"{missing} of {len(passages)} passages have no embedding yet and rank by keyword only "
                        "(run artmind ingest embed-passages)")
    ranked = rank(part, passages, embedding)
    rows, per = [], {}
    for p in ranked:
        if len(rows) >= top:
            break
        if per.get(p["doc_id"], 0) >= per_doc:
            continue
        per[p["doc_id"]] = per.get(p["doc_id"], 0) + 1
        m = meta.get(p["doc_id"]) or {}
        ref = brief.doc_ref(p["doc_id"], m.get("title"), m.get("name"), m.get("domain"), m.get("date"),
                            m.get("superseded"), m.get("supersedes"))
        row = brief.passage_row(len(rows) + 1, p.get("id"), p.get("chunk_id"), p.get("text"),
                                None if p["relevance"] is None else round(p["relevance"], 4), ref)
        row["matched"] = p["matched"]
        rows.append(row)
    nxt = None
    if rows and not any(r["matched"] for r in rows):
        warnings.append("no passage in the survey's documents contains the part's words; the rows match by meaning only")
        nxt = _survey_next(domains, part)
    elif not rows:
        nxt = _survey_next(domains, part)
    return {"query_type": "find", "handle": handle, "part": part, "survey_question": question,
            "domains": domains, "scope": {"documents": len(doc_ids), "passages": len(passages)},
            "warnings": warnings, "next": nxt, "rows": rows}


def _survey_next(domains: list[str], part: str) -> str:
    import shlex

    scope = " ".join(f"--domain {shlex.quote(d)}" for d in domains)
    return f"artmind query survey {scope} --shape brief {shlex.quote(part)}"

