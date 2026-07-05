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
