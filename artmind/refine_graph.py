"""Entity resolution and graph refinement: cluster similar names, propose via LLM.

The clustering and the merge-resolution PROMPT are one of the two same-as
proposers (the other is `conflicts.py`'s cross-domain adjudicator). Neither
applies anything directly any more — every proposal lands in the review
queue `artmind.sameas` owns, alongside the adjudicator's, and a human
approves via `sameas approve`.
"""

import difflib
import json
from collections import defaultdict
from pathlib import Path

from loguru import logger

from artmind import sameas
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


def cluster_entities_by_class(
    name_class_pairs: "list[tuple[str, str | None]]", similarity_threshold: float = 0.7
) -> list[list[str]]:
    """Cluster names within each entity_class only.

    Cross-class name similarity must never propose a merge: 'Premium Account'
    (PRODUCT) vs 'Premium Account Fee' (FEE) are 80% similar strings but
    different real-world things, and apoc mergeNodes would union their class
    labels into one frankenentity.
    """
    by_class: dict[str, list[str]] = defaultdict(list)
    for name, cls in name_class_pairs:
        by_class[cls or ""].append(name)
    clusters: list[list[str]] = []
    for _cls, names in sorted(by_class.items()):
        clusters.extend(cluster_entities(sorted(set(names)), similarity_threshold))
    return clusters


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


def _entity_domains(session, names: list[str]) -> dict[str, set[str]]:
    """Map each entity name to the set of domains it appears in."""
    rows = session.run(
        "MATCH (e:Entity) WHERE e.name IN $names RETURN e.name AS name, collect(DISTINCT e._domain) AS domains",
        names=names,
    ).data()
    return {r["name"]: set(r["domains"]) for r in rows}


def _entity_classes(session, names: list[str], domain: str | None) -> dict[str, set[str]]:
    """Map each entity name to its entity_class set (scoped to domain when given)."""
    rows = session.run(
        """
        MATCH (e:Entity)
        WHERE e.name IN $names AND ($domain IS NULL OR e._domain = $domain)
        RETURN e.name AS name, collect(DISTINCT e.entity_class) AS classes
        """,
        names=names,
        domain=domain,
    ).data()
    return {r["name"]: {c for c in r["classes"] if c} for r in rows}


def cross_class_pairs(
    proposed_merges: dict[str, str], class_map: dict[str, set[str]]
) -> dict[str, str]:
    """Return the alias→canonical pairs whose class sets don't intersect.

    Pairs with an unknown class on either side are NOT flagged — a missing
    entity_class shouldn't block a merge, only a positive mismatch should.
    """
    flagged: dict[str, str] = {}
    for alias, canonical in proposed_merges.items():
        a, c = class_map.get(alias, set()), class_map.get(canonical, set())
        if a and c and not (a & c):
            flagged[alias] = canonical
    return flagged


def _entity_keys(session, names: list[str], domain: str | None) -> dict[str, set[str]]:
    """Map each entity name to its aggregate-key string(s) (scoped to domain
    when given). More than one key means the name is ambiguous across domains
    or classes — such a name is skipped when proposing, same as `dmap`'s
    cross-domain guard already does for the merge decision itself."""
    rows = session.run(
        """
        MATCH (e:Entity)
        WHERE e.name IN $names AND ($domain IS NULL OR e._domain = $domain)
        RETURN e.name AS name, collect(DISTINCT e.key) AS keys
        """,
        names=names,
        domain=domain,
    ).data()
    return {r["name"]: {k for k in r["keys"] if k} for r in rows}


def propose_merges(
    proposed_merges: dict[str, str], domain: str | None, model: str
) -> dict:
    """Turn `{alias: canonical}` mappings into same-as proposals, grouped by
    canonical — one proposal per canonical, however many aliases map to it.

    The destructive `apoc.mergeNodes` apply step is gone: clustering and the
    merge PROMPT survive as a proposer, the apply does not (a human approves
    via `sameas approve`, same review queue conflicts.py's adjudicator feeds).
    Cross-class pairs are skipped here regardless of how the proposals were
    produced — clustering is class-constrained, but --from-file proposals may
    be hand-edited or predate that constraint, and a same-as group spanning
    two classes is exactly what groups (vs. pairs) exist to prevent.
    """
    stats = {"proposed": 0, "skipped": 0, "errors": 0, "skipped_cross_class": 0, "skipped_ambiguous": 0}
    proposal_ids: list[str] = []
    with neo4j_session() as session:
        names = list({*proposed_merges.keys(), *proposed_merges.values()})
        class_map = _entity_classes(session, names, domain) if names else {}
        key_map = _entity_keys(session, names, domain) if names else {}
        flagged = cross_class_pairs(proposed_merges, class_map)

        # Group by canonical so a cluster of 3+ aliases becomes ONE group.
        by_canonical: dict[str, list[str]] = {}
        for alias, canonical in proposed_merges.items():
            if alias == canonical:
                stats["skipped"] += 1
                continue
            if alias in flagged:
                stats["skipped_cross_class"] += 1
                logger.warning(
                    "Skipped cross-class proposal: {} ({}) → {} ({})",
                    alias, sorted(class_map.get(alias, set())),
                    canonical, sorted(class_map.get(canonical, set())),
                )
                continue
            by_canonical.setdefault(canonical, []).append(alias)

        for canonical, aliases in by_canonical.items():
            canonical_keys = key_map.get(canonical, set())
            if len(canonical_keys) != 1:
                stats["skipped_ambiguous"] += len(aliases)
                logger.warning(
                    "Skipped proposal for canonical {!r}: {} distinct key(s) found "
                    "(expected exactly 1)", canonical, len(canonical_keys),
                )
                continue
            canonical_key = _parse_key(next(iter(canonical_keys)))
            member_keys = [canonical_key]
            for alias in aliases:
                alias_keys = key_map.get(alias, set())
                if len(alias_keys) != 1:
                    stats["skipped_ambiguous"] += 1
                    logger.warning(
                        "Skipped alias {!r} -> {!r}: {} distinct key(s) found for the alias "
                        "(expected exactly 1)", alias, canonical, len(alias_keys),
                    )
                    continue
                member_keys.append(_parse_key(next(iter(alias_keys))))
            if len(member_keys) < 2:
                continue
            pid = sameas.propose(
                session, canonical_key, member_keys,
                source="refine_graph", reason="name-similarity clustering", model=model,
            )
            proposal_ids.append(pid)
            stats["proposed"] += 1
    return {"stats": stats, "proposal_ids": proposal_ids}


def _parse_key(value: str) -> tuple[str, str, str]:
    parts = value.split("|")
    return tuple(parts) if len(parts) == 3 else (value, "", "")  # type: ignore[return-value]


def refine_graph(
    domain: str | None,
    name_filter: str | None,
    model: str,
    similarity_threshold: float,
    dry_run: bool,
    output_file: Path | None,
    from_file: Path | None,
    allow_cross_domain_merge: bool = False,
) -> dict:
    """Cluster similar entity names, ask LLM which to merge, optionally apply to Neo4j.

    dry_run=True  → compute proposals and write output_file, do NOT apply
    dry_run=False → compute proposals, write output_file if given, then apply
    from_file     → skip computation, load proposals from file and apply (ignores dry_run)
    allow_cross_domain_merge → when domain is None (all-domains run), allow merges whose
        alias/canonical span more than one domain. Default False: such merges are dropped
        and recorded in report["skipped_cross_domain"] instead of being applied.
    """
    from utils.functions import load_env

    report: dict = {"proposed_merges": {}, "stats": {}}

    if from_file:
        data = json.loads(from_file.read_text(encoding="utf-8"))
        proposed_merges: dict[str, str] = data.get("proposed_merges", data)
        logger.info("Loaded {} merge proposal(s) from {}", len(proposed_merges), from_file)

        result = propose_merges(proposed_merges, domain, model)
        report["proposed_merges"] = proposed_merges
        report["stats"] = result["stats"]
        report["proposal_ids"] = result["proposal_ids"]
        logger.info("Done — {}", result["stats"])
        return report

    env = load_env()
    timeout = int(env.get("ARTMIND_OLLAMA_TIMEOUT", "120"))

    # Fetch distinct (name, entity_class) pairs — clustering is per-class so
    # cross-class name lookalikes can never end up in the same merge cluster.
    with neo4j_session() as session:
        name_filters = [n.strip() for n in name_filter.split(",")] if name_filter else []
        if name_filters:
            # Use exact match for each filtered name (case-insensitive via CONTAINS)
            if domain:
                res = session.run(
                    f"""MATCH (e:{ENTITY_NODE_LABEL} {{_domain: $domain}})
                    WHERE any(name IN $name_filters WHERE toLower(e.name) CONTAINS toLower(name))
                    RETURN DISTINCT e.name AS name, e.entity_class AS entity_class""",
                    domain=domain,
                    name_filters=name_filters,
                )
            else:
                res = session.run(
                    """MATCH (e:Entity)
                    WHERE any(name IN $name_filters WHERE toLower(e.name) CONTAINS toLower(name))
                    RETURN DISTINCT e.name AS name, e.entity_class AS entity_class""",
                    name_filters=name_filters,
                )
        elif domain:
            res = session.run(
                f"MATCH (e:{ENTITY_NODE_LABEL} {{_domain: $domain}}) "
                "RETURN DISTINCT e.name AS name, e.entity_class AS entity_class",
                domain=domain,
            )
        else:
            res = session.run(
                f"MATCH (e:{ENTITY_NODE_LABEL}) "
                "RETURN DISTINCT e.name AS name, e.entity_class AS entity_class"
            )
        all_entities = [(r["name"], r["entity_class"]) for r in res if r["name"]]

    logger.info("Fetched {} unique (name, class) pair(s) from graph", len(all_entities))

    clusters = cluster_entities_by_class(all_entities, similarity_threshold)
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

    # Domain lookup for the cross-domain guard below — nothing here mutates
    # the graph any more (proposing is additive), so there's no staleness
    # risk in computing it once and reusing it.
    dmap: dict[str, set[str]] = {}
    if report["proposed_merges"]:
        names = set(report["proposed_merges"].keys()) | set(report["proposed_merges"].values())
        with neo4j_session() as session:
            dmap = _entity_domains(session, list(names))

    if domain is None and not allow_cross_domain_merge and report["proposed_merges"]:
        kept, skipped = {}, {}
        for alias, canonical in report["proposed_merges"].items():
            spans = dmap.get(alias, set()) | dmap.get(canonical, set())
            if len(spans) > 1:
                skipped[alias] = canonical
            else:
                kept[alias] = canonical
        report["proposed_merges"] = kept
        report["skipped_cross_domain"] = skipped
        if skipped:
            logger.info("Skipped {} cross-domain merge cluster(s): {}", len(skipped), skipped)

    if output_file:
        output_file.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        logger.info("Proposals written to {}", output_file)

    if not dry_run and report["proposed_merges"]:
        result = propose_merges(report["proposed_merges"], domain, model)
        report["stats"] = result["stats"]
        report["proposal_ids"] = result["proposal_ids"]
        logger.info("Done — {}", result["stats"])

    return report
