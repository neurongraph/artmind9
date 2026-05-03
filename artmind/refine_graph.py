"""Entity resolution and graph refinement: cluster similar names, merge via LLM, apply to Neo4j."""

import difflib
import json
from collections import defaultdict
from pathlib import Path

from loguru import logger

from artmind.graph_query import neo4j_session
from artmind.ingest import _call_llm_text, _parse_json_response

ENTITY_NODE_LABEL = "Entity"

_MERGE_PROMPT = """You are an entity resolution assistant. The following entity names may refer to the same real-world entity or person:

{names_list}

Decide which names refer to the same entity and provide a canonical (preferred) name for each.
The canonical name should be the most complete and formal version.
Only merge names that clearly refer to the same entity. If names are clearly different entities, do NOT merge them.

Return a JSON object where each key is one of the input names and the value is the canonical name it maps to.
If a name is already the canonical form, map it to itself.

Example:
{{
  "Holmes": "Sherlock Holmes",
  "Sherlock Holmes": "Sherlock Holmes",
  "Miss Hunter": "Violet Hunter",
  "Violet Hunter": "Violet Hunter",
  "Watson": "Watson"
}}

JSON only, no explanation:"""


def cluster_entities(names: list[str], similarity_threshold: float = 0.7) -> list[list[str]]:
    """Group entity names into clusters via string-similarity union-find."""
    n = len(names)
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x: int, y: int) -> None:
        rx, ry = find(x), find(y)
        if rx != ry:
            parent[rx] = ry

    for i in range(n):
        for j in range(i + 1, n):
            ratio = difflib.SequenceMatcher(None, names[i].lower(), names[j].lower()).ratio()
            if ratio >= similarity_threshold:
                union(i, j)

    groups: dict[int, list[str]] = defaultdict(list)
    for i, name in enumerate(names):
        groups[find(i)].append(name)
    return list(groups.values())


def llm_merge_cluster(cluster: list[str], model: str, timeout: int) -> dict[str, str]:
    """Ask LLM which names in a cluster are aliases and what the canonical name is."""
    names_list = "\n".join(f"  - {name}" for name in cluster)
    prompt = _MERGE_PROMPT.format(names_list=names_list)
    try:
        raw = _call_llm_text(model, prompt)
        result = _parse_json_response(raw)
        if isinstance(result, dict):
            # Only return mappings for names actually in the cluster
            return {str(k): str(v) for k, v in result.items() if k in cluster}
    except Exception as e:
        logger.warning("LLM merge cluster failed for {}: {}", cluster, e)
    return {name: name for name in cluster}


def _merge_entity_pair(session, alias: str, canonical: str, domain: str | None) -> bool:
    """Merge alias entity node into canonical entity node, re-wiring all relationships."""
    params: dict = {"alias": alias, "canonical": canonical}
    domain_clause = ""
    if domain:
        params["domain"] = domain
        domain_clause = ", domain: $domain"

    try:
        # Add alias name to canonical's aliases list before merge
        session.run(
            f"""
            MATCH (canonical:Entity {{name: $canonical{domain_clause}}})
            SET canonical.aliases =
                CASE WHEN $alias IN coalesce(canonical.aliases, [])
                     THEN coalesce(canonical.aliases, [])
                     ELSE coalesce(canonical.aliases, []) + [$alias]
                END
            """,
            **params,
        )

        # APOC mergeNodes: re-wires all rels from alias to canonical, then deletes alias
        result = session.run(
            f"""
            MATCH (alias:Entity {{name: $alias{domain_clause}}})
            MATCH (canonical:Entity {{name: $canonical{domain_clause}}})
            WHERE alias <> canonical
            CALL apoc.refactor.mergeNodes([canonical, alias], {{properties: 'discard', mergeRels: true}}) YIELD node
            SET node.name = $canonical
            RETURN node.name AS name
            """,
            **params,
        )
        merged = [r["name"] for r in result]
        if not merged:
            logger.warning("No nodes merged for {} → {} (nodes may not exist or already merged)", alias, canonical)
            return False
        return True
    except Exception as e:
        logger.error("Failed to merge {} → {}: {}", alias, canonical, e)
        return False


def apply_merges(proposed_merges: dict[str, str], domain: str | None) -> dict:
    """Execute entity merges in Neo4j. Returns stats dict."""
    stats = {"merged": 0, "skipped": 0, "errors": 0}
    with neo4j_session() as session:
        for alias, canonical in proposed_merges.items():
            if alias == canonical:
                stats["skipped"] += 1
                continue
            ok = _merge_entity_pair(session, alias, canonical, domain)
            if ok:
                stats["merged"] += 1
                logger.info("Merged: {} → {}", alias, canonical)
            else:
                stats["errors"] += 1
    return stats


def refine_graph(
    domain: str | None,
    model: str,
    similarity_threshold: float,
    dry_run: bool,
    output_file: Path | None,
    from_file: Path | None,
) -> dict:
    """Cluster similar entity names, ask LLM which to merge, optionally apply to Neo4j.

    dry_run=True  → compute proposals and write output_file, do NOT apply
    dry_run=False → compute proposals, write output_file if given, then apply
    from_file     → skip computation, load proposals from file and apply (ignores dry_run)
    """
    from utils.functions import load_env

    report: dict = {"proposed_merges": {}, "stats": {}}

    if from_file:
        data = json.loads(from_file.read_text(encoding="utf-8"))
        proposed_merges: dict[str, str] = data.get("proposed_merges", data)
        logger.info("Loaded {} merge proposal(s) from {}", len(proposed_merges), from_file)
        stats = apply_merges(proposed_merges, domain)
        report["proposed_merges"] = proposed_merges
        report["stats"] = stats
        logger.info("Done — merged={merged} skipped={skipped} errors={errors}", **stats)
        return report

    env = load_env()
    timeout = int(env.get("ARTMIND_OLLAMA_TIMEOUT", "120"))

    # Fetch distinct entity names from the graph
    with neo4j_session() as session:
        if domain:
            res = session.run(
                f"MATCH (e:{ENTITY_NODE_LABEL} {{domain: $domain}}) RETURN DISTINCT e.name AS name",
                domain=domain,
            )
        else:
            res = session.run(f"MATCH (e:{ENTITY_NODE_LABEL}) RETURN DISTINCT e.name AS name")
        all_entities = [r["name"] for r in res if r["name"]]

    logger.info("Fetched {} unique entity name(s) from graph", len(all_entities))

    clusters = cluster_entities(all_entities, similarity_threshold)
    multi_clusters = [c for c in clusters if len(c) > 1]
    logger.info(
        "{} single-entity clusters, {} multi-entity clusters (merge candidates)",
        len(clusters) - len(multi_clusters),
        len(multi_clusters),
    )

    for i, cluster in enumerate(multi_clusters, 1):
        preview = cluster[:3]
        suffix = "..." if len(cluster) > 3 else ""
        logger.info("[{}/{}] Processing cluster of {}: {}{}", i, len(multi_clusters), len(cluster), preview, suffix)
        merges = llm_merge_cluster(cluster, model, timeout)
        actual = {alias: canonical for alias, canonical in merges.items() if alias != canonical}
        if actual:
            report["proposed_merges"].update(actual)
            logger.info("  → {} merge(s) proposed: {}", len(actual), actual)

    logger.info("Total proposed merges: {}", len(report["proposed_merges"]))

    if output_file:
        output_file.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        logger.info("Proposals written to {}", output_file)

    if not dry_run and report["proposed_merges"]:
        stats = apply_merges(report["proposed_merges"], domain)
        report["stats"] = stats
        logger.info("Done — merged={merged} skipped={skipped} errors={errors}", **stats)

    return report
