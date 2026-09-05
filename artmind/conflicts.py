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

from artmind.graph_query import expand_domain_family, neo4j_session
from artmind.ingest import _call_llm_text, _parse_json_response
from artmind.temporal import apply_supersession


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def conflict_id(id_a: str, id_b: str, aspect: str) -> str:
    lo, hi = sorted([id_a, id_b])
    return hashlib.sha1(f"{lo}|{hi}|{_slug(aspect)}".encode("utf-8")).hexdigest()


def _name_ratio(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio()


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
    # `domains` arrives already expanded by detect_conflicts (see
    # expand_domain_family), so a parent like `banking` reaches its concrete
    # children here and cross-child pairing works as intended.
    seen: set[tuple[str, str]] = set()
    scored: list[tuple[float, float, dict]] = []
    with neo4j_session() as session:
        fetch = """
        MATCH (e:Entity)
        WHERE e._domain IN $domains AND e.embedding IS NOT NULL AND e.name IS NOT NULL
          AND ($nameFilter IS NULL OR toLower(e.name) CONTAINS toLower($nameFilter))
        RETURN e._id AS id, e.name AS name, e.entity_class AS entity_class,
               e._domain AS domain, e.embedding AS embedding, e.key AS key
        """
        sources = session.run(
            fetch, domains=domains, nameFilter=name_filter
        ).data()
        for src in sources:
            others = [d for d in domains if d != src["domain"]] or domains
            neighbors = session.run(
                """
                CYPHER 25
                MATCH (node:Entity)
                  SEARCH node IN (
                    VECTOR INDEX entity_embedding
                    FOR $embedding
                    LIMIT $k
                  )
                WHERE node._domain IN $others
                  AND node.entity_class = $cls
                  AND node._id <> $srcId
                WITH node, vector.similarity.cosine(node.embedding, $embedding) AS score
                RETURN node._id AS id, node.name AS name, node._domain AS domain,
                       node.key AS key, score
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
                        "key_a": src.get("key"),
                        "id_b": nb["id"], "name_b": nb["name"], "domain_b": nb["domain"],
                        "key_b": nb.get("key"),
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

Document A valid_from={valid_from_a} version={version_a}; Document B valid_from={valid_from_b} version={version_b}.
If one side is a NEWER REVISION OF THE SAME AUTHORITY (same document lineage, later
valid_from/version), return verdict "superseded" instead of "conflicting_claims".
Decide the relationship. Return ONLY JSON with these keys:
- "verdict": one of "same_entity_consistent" | "conflicting_claims" | "unrelated" | "superseded"
- "aspect": short phrase naming the disputed dimension (e.g. "fee reversal approval limit")
- "claim_a": A's specific claim on that aspect (short)
- "claim_b": B's specific claim on that aspect (short)
- "severity": "high" | "medium" | "low"
Only return "conflicting_claims" when the two claims genuinely cannot both be true.
JSON only:"""


def gather_evidence(session, entity_id: str, max_chunks: int) -> list[dict]:
    """Top-k source chunks an entity was extracted from, truncated for bounded LLM cost.

    Reached via `(Entity)-[:AGGREGATES]->(Observation)-[:EXTRACTED_FROM]->(DocChunk)`
    (Phase 4) — provenance moved onto observations in Phase 3, so an Entity
    itself has never carried a direct EXTRACTED_FROM edge; matching one on the
    Entity (as this query did before) silently returned zero chunks always.
    :MENTIONS is never created for document chunks; it's only written for
    (UserChat)-[:MENTIONS]->(Entity) by the artmind-update chat path.
    """
    return session.run(
        """
        MATCH (e:Entity {_id:$id})-[:AGGREGATES]->(:Observation)-[:EXTRACTED_FROM]->(c:DocChunk)
        RETURN DISTINCT c.id AS id, c.doc_id AS doc_id, c.name AS name, c._domain AS domain,
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


def _doc_validity(session, evidence: list[dict]) -> tuple[str, str]:
    """Best-effort (valid_from, version) of the Document behind the first evidence chunk.

    Defensive: returns ("", "") on any lookup failure or missing properties, since
    valid_from/version may not exist for untimed documents.
    """
    if not evidence:
        return "", ""
    doc_id = evidence[0].get("doc_id")
    if not doc_id:
        return "", ""
    try:
        rec = session.run(
            "MATCH (d:Document {id:$id}) RETURN d.valid_from AS valid_from, d.version AS version",
            id=doc_id,
        ).single()
        if not rec:
            return "", ""
        return str(rec["valid_from"] or ""), str(rec["version"] or "")
    except Exception:
        return "", ""


def llm_adjudicate(session, pair: dict, evidence_a: list[dict], evidence_b: list[dict], model: str) -> dict:
    valid_from_a, version_a = _doc_validity(session, evidence_a)
    valid_from_b, version_b = _doc_validity(session, evidence_b)
    prompt = _ADJUDICATE_PROMPT.format(
        domain_a=pair["domain_a"], name_a=pair["name_a"],
        domain_b=pair["domain_b"], name_b=pair["name_b"],
        evidence_a="\n".join(f"- {c['text']}" for c in evidence_a) or "(none)",
        evidence_b="\n".join(f"- {c['text']}" for c in evidence_b) or "(none)",
        valid_from_a=valid_from_a, version_a=version_a,
        valid_from_b=valid_from_b, version_b=version_b,
    )
    try:
        raw = _call_llm_text(model, prompt)
    except Exception as e:
        logger.warning("adjudicate LLM failed for {}: {}", pair.get("aspect"), e)
        return {"verdict": "unrelated", "aspect": "", "claim_a": "", "claim_b": "", "severity": "low"}
    return _verdict_from_raw(raw)


def materialize(session, pair: dict, verdict: dict, evidence_a: list[dict], evidence_b: list[dict], model: str) -> str | None:
    """MERGE-only write of a Conflict, a SameAsProposal, or nothing — the
    "two outcomes" of one proposer's judgment. Returns the id written, if any.

    `"same_entity_consistent"` used to be discarded; it now proposes a
    same-as group instead, in the SAME review queue `refine_graph.py`'s
    clustering proposer feeds (`artmind.sameas`) — a curated group is a
    (class, domain)-aware assertion, so `pair["key_a"]`/`key_b"]` (the raw
    aggregate keys, not just entity ids) are what a proposal actually needs.
    """
    if verdict["verdict"] == "same_entity_consistent":
        if not pair.get("key_a") or not pair.get("key_b"):
            logger.warning(
                "same_entity_consistent verdict for {} <-> {} but one side has no "
                "Entity.key; skipped (pre-Phase-6 entity, or the key property is missing)",
                pair.get("name_a"), pair.get("name_b"),
            )
            return None
        from artmind import sameas

        key_a, key_b = tuple(pair["key_a"].split("|")), tuple(pair["key_b"].split("|"))
        # No canonical opinion from the adjudicator — the LONGER name is the
        # cheapest reasonable default, and the human reviewing `sameas list`
        # can always override it at approval time.
        canonical, other = (key_a, key_b) if len(pair["name_a"]) >= len(pair["name_b"]) else (key_b, key_a)
        return sameas.propose(
            session, canonical, [other],
            source="adjudicator", reason=verdict.get("aspect") or "cross-domain identity match", model=model,
        )
    if verdict["verdict"] == "superseded":
        # Resolve entity ids to their documents and record SUPERSEDES + valid_to.
        # Reached via AGGREGATES->Observation->EXTRACTED_FROM (Phase 4) — see
        # gather_evidence's docstring for why; :MENTIONS is NOT used here (it is
        # never created for document chunks).
        rec = session.run(
            """
            MATCH (a:Entity {_id:$idA})-[:AGGREGATES]->(:Observation)-[:EXTRACTED_FROM]->(:DocChunk)-[:PART_OF]->(da:Document)
            MATCH (b:Entity {_id:$idB})-[:AGGREGATES]->(:Observation)-[:EXTRACTED_FROM]->(:DocChunk)-[:PART_OF]->(db:Document)
            RETURN da.id AS a, da.valid_from AS af, db.id AS b, db.valid_from AS bf
            LIMIT 1
            """,
            idA=pair["id_a"], idB=pair["id_b"],
        ).single()
        if rec and rec["a"] and rec["b"]:
            # newer = later valid_from
            if (rec.get("bf") or "") >= (rec.get("af") or ""):
                apply_supersession(rec["b"], rec["a"], "document", rec.get("bf"), detected_by="adjudicator")
            else:
                apply_supersession(rec["a"], rec["b"], "document", rec.get("af"), detected_by="adjudicator")
        return None
    if verdict["verdict"] != "conflicting_claims":
        return None
    cid = conflict_id(pair["id_a"], pair["id_b"], verdict["aspect"] or pair["entity_class"])
    domains = sorted({pair["domain_a"], pair["domain_b"]})
    session.run(
        """
        MERGE (co:Conflict {id:$id})
        ON CREATE SET co.aspect=$aspect, co.claim_a=$claim_a, co.claim_b=$claim_b,
                      co.severity=$severity, co.status='open', co.domains=$domains,
                      co.detected_at=$now, co.detected_by_model=$model,
                      co._source='adjudicator'
        WITH co
        MATCH (a:Entity {_id:$idA}), (b:Entity {_id:$idB})
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
    requested = list(domains)
    domains = []
    for d in requested:
        for expanded in expand_domain_family(d):
            if expanded not in domains:
                domains.append(expanded)

    report: dict = {"domains": domains, "candidates": 0, "llm_calls": 0,
                    "conflicts": [], "same_as_proposals": [], "stats": {},
                    "candidate_seconds": 0.0, "llm_seconds": 0.0}
    report["domains_requested"] = requested

    def _record(item: dict, materialized_id: str) -> None:
        if item["verdict"]["verdict"] == "same_entity_consistent":
            report["same_as_proposals"].append(materialized_id)
        else:
            report["conflicts"].append(materialized_id)

    if from_file:
        data = json.loads(Path(from_file).read_text(encoding="utf-8"))
        with neo4j_session() as session:
            for item in data.get("conflicts", []):
                cid = materialize(session, item["pair"], item["verdict"],
                                  item["evidence_a"], item["evidence_b"], item.get("model", model))
                if cid:
                    _record(item, cid)
        report["stats"] = {"materialized": len(report["conflicts"]) + len(report["same_as_proposals"])}
        return report

    import time

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
            verdict = llm_adjudicate(session, pair, ev_a, ev_b, model)
            report["llm_calls"] += 1
            if verdict["verdict"] in ("conflicting_claims", "superseded", "same_entity_consistent"):
                # All three flow through the same dry-run/apply pipeline;
                # materialize() discriminates them (Conflict node, SUPERSEDES
                # edge, or a SameAsProposal — "one proposer, two outcomes").
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
                    _record(item, cid)
        report["stats"] = {"materialized": len(report["conflicts"]) + len(report["same_as_proposals"])}

    logger.info(
        "detect-conflicts: candidates={} llm_calls={} conflicts={} same_as_proposals={} (cand={}s llm={}s)",
        report["candidates"], report["llm_calls"], len(report["conflicts"]), len(report["same_as_proposals"]),
        report["candidate_seconds"], report["llm_seconds"],
    )
    return report


RESOLUTION_STATUSES = ("resolved", "dismissed")


def resolve_conflict(conflict_id: str, status: str, reason: str | None = None) -> dict:
    """Close a materialized conflict as resolved or dismissed.

    Detection is deliberately one-way: materialize() only ever creates
    conflicts with status 'open', and nothing closes them automatically. A
    conflict represents two authorities genuinely disagreeing, which a
    re-detection pass cannot adjudicate — closing it is a human judgment, so it
    is an explicit command and never a side effect.

    Raises ValueError when the id matches no Conflict node. That includes the
    orphaned-edge case: a CONFLICTS_WITH edge whose Conflict node was deleted
    still surfaces in list_conflicts (reported as 'open' via coalesce) but has
    nowhere to record a status.
    """
    if status not in RESOLUTION_STATUSES:
        raise ValueError(
            f"status must be one of {', '.join(RESOLUTION_STATUSES)}; got {status!r}"
        )
    with neo4j_session() as session:
        rec = session.run(
            """
            MATCH (co:Conflict {id: $id})
            SET co.status = $status,
                co.resolved_at = $now,
                co.resolution_reason = $reason
            RETURN co.id AS id, co.status AS status
            """,
            id=conflict_id,
            status=status,
            now=datetime.now(timezone.utc).isoformat(),
            reason=reason,
        ).single()
    if not rec:
        raise ValueError(
            f"No Conflict node with id {conflict_id!r}. If `query graph conflicts` "
            "listed it, the row may come from an orphaned CONFLICTS_WITH edge whose "
            "Conflict node no longer exists — such rows report as 'open' but carry no status."
        )
    logger.info("conflict {} → {} ({})", conflict_id, status, reason or "no reason given")
    return {"id": rec["id"], "status": rec["status"], "reason": reason}
