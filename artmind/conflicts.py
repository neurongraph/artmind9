"""Non-destructive cross-domain conflict detection.

Candidate pairing is NOT a brute-force cross-product: block by entity_class,
generate candidates via the entity_embedding ANN index (top-k per entity,
restricted to the other domain(s)), and use difflib name ratio only as a
secondary tie-break on the ANN shortlist. Materialization only ever CREATEs
annotations (Conflict nodes + CONFLICTS_WITH/CONFLICT_OF/EVIDENCE edges).
"""
import difflib
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger

from artmind.graph_query import neo4j_session
from artmind.ingest import _call_llm_text, _parse_json_response


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def conflict_id(id_a: str, id_b: str, aspect: str) -> str:
    lo, hi = sorted([id_a, id_b])
    return hashlib.sha1(f"{lo}|{hi}|{_slug(aspect)}".encode("utf-8")).hexdigest()


def _name_ratio(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio()


def check_refine_precondition(session, domains: list[str]) -> list[str]:
    """Return the subset of domains with NO recorded refine-graph run."""
    rows = session.run(
        "MATCH (r:RefineRun) WHERE r.domain IN $domains RETURN collect(r.domain) AS done",
        domains=domains,
    ).single()
    done = set(rows["done"]) if rows else set()
    return [d for d in domains if d not in done]


def candidate_pairs(
    domains: list[str],
    name_filter: str | None,
    sim_threshold: float,
    max_pairs: int,
    top_k: int = 10,
) -> list[dict]:
    """Generate cross-domain candidate entity pairs.

    1. Fetch entities with embeddings, grouped by (domain, entity_class).
    2. For each entity in domain A, ANN-query the entity_embedding index for the
       top_k nearest entities of the SAME class restricted to the OTHER domains.
    3. Keep pairs with cosine score >= sim_threshold; difflib name ratio is a
       secondary tie-break added to the sort key, never the primary generator.
    Deterministic dedupe by (min_id,max_id); truncated to max_pairs.
    """
    seen: set[tuple[str, str]] = set()
    scored: list[tuple[float, float, dict]] = []
    with neo4j_session() as session:
        fetch = """
        MATCH (e:Entity)
        WHERE e.domain IN $domains AND e.embedding IS NOT NULL AND e.name IS NOT NULL
          AND ($nameFilter IS NULL OR toLower(e.name) CONTAINS toLower($nameFilter))
        RETURN e.id AS id, e.name AS name, e.entity_class AS entity_class,
               e.domain AS domain, e.embedding AS embedding
        """
        sources = session.run(
            fetch, domains=domains, nameFilter=name_filter
        ).data()
        for src in sources:
            others = [d for d in domains if d != src["domain"]] or domains
            neighbors = session.run(
                """
                CALL db.index.vector.queryNodes('entity_embedding', $k, $embedding)
                YIELD node, score
                WHERE node.domain IN $others
                  AND node.entity_class = $cls
                  AND node.id <> $srcId
                RETURN node.id AS id, node.name AS name, node.domain AS domain, score
                """,
                k=top_k, embedding=src["embedding"], others=others,
                cls=src["entity_class"], srcId=src["id"],
            ).data()
            for nb in neighbors:
                if nb["score"] < sim_threshold:
                    continue
                key = tuple(sorted([src["id"], nb["id"]]))
                if key in seen:
                    continue
                seen.add(key)
                nr = _name_ratio(src["name"], nb["name"])
                scored.append((
                    nb["score"], nr,
                    {
                        "id_a": src["id"], "name_a": src["name"], "domain_a": src["domain"],
                        "id_b": nb["id"], "name_b": nb["name"], "domain_b": nb["domain"],
                        "entity_class": src["entity_class"],
                        "sim": round(nb["score"], 4), "name_ratio": round(nr, 4),
                    },
                ))
    scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
    return [item[2] for item in scored[:max_pairs]]


_ADJUDICATE_PROMPT = """You are a conflict-detection assistant for a knowledge graph.
Two entities from different domains may describe the same real-world thing and may
make contradictory quantitative or authority claims.

ENTITY A ({domain_a}) — {name_a}
Evidence A:
{evidence_a}

ENTITY B ({domain_b}) — {name_b}
Evidence B:
{evidence_b}

Decide the relationship. Return ONLY JSON with these keys:
- "verdict": one of "same_entity_consistent" | "conflicting_claims" | "unrelated"
- "aspect": short phrase naming the disputed dimension (e.g. "fee reversal approval limit")
- "claim_a": A's specific claim on that aspect (short)
- "claim_b": B's specific claim on that aspect (short)
- "severity": "high" | "medium" | "low"
Only return "conflicting_claims" when the two claims genuinely cannot both be true.
JSON only:"""


def gather_evidence(session, entity_id: str, max_chunks: int) -> list[dict]:
    """Top-k MENTIONS chunks for an entity, truncated for bounded LLM cost."""
    return session.run(
        """
        MATCH (c:DocChunk)-[:MENTIONS]->(e:Entity {id:$id})
        RETURN c.id AS id, c.doc_id AS doc_id, c.name AS name, c.domain AS domain,
               left(c.text, 1200) AS text
        LIMIT $k
        """,
        id=entity_id, k=max_chunks,
    ).data()


def _verdict_from_raw(raw: str) -> dict:
    default = {"verdict": "unrelated", "aspect": "", "claim_a": "", "claim_b": "", "severity": "low"}
    try:
        parsed = _parse_json_response(raw)
        if isinstance(parsed, list):
            parsed = parsed[0] if parsed else {}
        if not isinstance(parsed, dict):
            return default
        v = parsed.get("verdict", "unrelated")
        if v not in ("same_entity_consistent", "conflicting_claims", "unrelated", "superseded"):
            v = "unrelated"
        return {
            "verdict": v,
            "aspect": str(parsed.get("aspect", "")),
            "claim_a": str(parsed.get("claim_a", "")),
            "claim_b": str(parsed.get("claim_b", "")),
            "severity": parsed.get("severity", "low") if parsed.get("severity") in ("high", "medium", "low") else "low",
        }
    except Exception:
        return default


def llm_adjudicate(pair: dict, evidence_a: list[dict], evidence_b: list[dict], model: str) -> dict:
    prompt = _ADJUDICATE_PROMPT.format(
        domain_a=pair["domain_a"], name_a=pair["name_a"],
        domain_b=pair["domain_b"], name_b=pair["name_b"],
        evidence_a="\n".join(f"- {c['text']}" for c in evidence_a) or "(none)",
        evidence_b="\n".join(f"- {c['text']}" for c in evidence_b) or "(none)",
    )
    try:
        raw = _call_llm_text(model, prompt)
    except Exception as e:
        logger.warning("adjudicate LLM failed for {}: {}", pair.get("aspect"), e)
        return {"verdict": "unrelated", "aspect": "", "claim_a": "", "claim_b": "", "severity": "low"}
    return _verdict_from_raw(raw)


def materialize(session, pair: dict, verdict: dict, evidence_a: list[dict], evidence_b: list[dict], model: str) -> str | None:
    """MERGE-only write of a Conflict + edges. Returns conflict id or None."""
    if verdict["verdict"] != "conflicting_claims":
        return None
    cid = conflict_id(pair["id_a"], pair["id_b"], verdict["aspect"] or pair["entity_class"])
    domains = sorted({pair["domain_a"], pair["domain_b"]})
    session.run(
        """
        MERGE (co:Conflict {id:$id})
        ON CREATE SET co.aspect=$aspect, co.claim_a=$claim_a, co.claim_b=$claim_b,
                      co.severity=$severity, co.status='open', co.domains=$domains,
                      co.detected_at=$now, co.detected_by_model=$model
        WITH co
        MATCH (a:Entity {id:$idA}), (b:Entity {id:$idB})
        MERGE (co)-[:CONFLICT_OF]->(a)
        MERGE (co)-[:CONFLICT_OF]->(b)
        MERGE (a)-[ra:CONFLICTS_WITH]->(b) SET ra.conflict_id=$id, ra.aspect=$aspect
        MERGE (b)-[rb:CONFLICTS_WITH]->(a) SET rb.conflict_id=$id, rb.aspect=$aspect
        """,
        id=cid, aspect=verdict["aspect"], claim_a=verdict["claim_a"], claim_b=verdict["claim_b"],
        severity=verdict["severity"], domains=domains,
        now=datetime.now(timezone.utc).isoformat(), model=model,
        idA=pair["id_a"], idB=pair["id_b"],
    )
    for side, chunks in (("a", evidence_a), ("b", evidence_b)):
        for c in chunks:
            session.run(
                """
                MATCH (co:Conflict {id:$id}), (c:DocChunk {id:$cid})
                MERGE (co)-[e:EVIDENCE {side:$side}]->(c)
                """,
                id=cid, cid=c["id"], side=side,
            )
    return cid


def detect_conflicts(
    domains: list[str],
    name_filter: str | None = None,
    sim_threshold: float = 0.75,
    max_pairs: int = 200,
    max_chunks_per_side: int = 2,
    model: str = "",
    dry_run: bool = False,
    output_file: Path | None = None,
    from_file: Path | None = None,
) -> dict:
    """Two-phase orchestrator mirroring refine-graph's dry-run/apply workflow."""
    report: dict = {"domains": domains, "candidates": 0, "llm_calls": 0,
                    "conflicts": [], "stats": {}, "candidate_seconds": 0.0, "llm_seconds": 0.0}

    if from_file:
        data = json.loads(Path(from_file).read_text(encoding="utf-8"))
        with neo4j_session() as session:
            for item in data.get("conflicts", []):
                cid = materialize(session, item["pair"], item["verdict"],
                                  item["evidence_a"], item["evidence_b"], item.get("model", model))
                if cid:
                    report["conflicts"].append(cid)
        report["stats"] = {"materialized": len(report["conflicts"])}
        return report

    import time
    # Precondition: warn if a target domain has no recorded refine-graph run.
    with neo4j_session() as session:
        missing = check_refine_precondition(session, domains)
    if missing:
        logger.warning(
            "detect-conflicts: no refine-graph run recorded for {} — run intra-domain "
            "refine-graph first, or candidate pairing will operate on raw chunk-level duplicates",
            missing,
        )
        report["warning_missing_refine"] = missing

    t0 = time.monotonic()
    pairs = candidate_pairs(domains, name_filter, sim_threshold, max_pairs)
    report["candidate_seconds"] = round(time.monotonic() - t0, 3)
    report["candidates"] = len(pairs)

    t1 = time.monotonic()
    proposals: list[dict] = []
    with neo4j_session() as session:
        for pair in pairs:
            ev_a = gather_evidence(session, pair["id_a"], max_chunks_per_side)
            ev_b = gather_evidence(session, pair["id_b"], max_chunks_per_side)
            verdict = llm_adjudicate(pair, ev_a, ev_b, model)
            report["llm_calls"] += 1
            if verdict["verdict"] == "conflicting_claims":
                proposals.append({"pair": pair, "verdict": verdict,
                                  "evidence_a": ev_a, "evidence_b": ev_b, "model": model})
    report["llm_seconds"] = round(time.monotonic() - t1, 3)
    report["proposals"] = proposals

    if output_file:
        Path(output_file).write_text(
            json.dumps({"domains": domains, "conflicts": proposals}, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    if not dry_run and proposals:
        with neo4j_session() as session:
            for item in proposals:
                cid = materialize(session, item["pair"], item["verdict"],
                                  item["evidence_a"], item["evidence_b"], model)
                if cid:
                    report["conflicts"].append(cid)
        report["stats"] = {"materialized": len(report["conflicts"])}

    logger.info(
        "detect-conflicts: candidates={} llm_calls={} materialized={} (cand={}s llm={}s)",
        report["candidates"], report["llm_calls"], len(report["conflicts"]),
        report["candidate_seconds"], report["llm_seconds"],
    )
    return report
