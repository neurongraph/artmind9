import re
from contextlib import contextmanager
from typing import Any, Sequence

from neo4j import GraphDatabase
from neo4j.graph import Node, Path, Relationship

from utils.functions import load_env


LABEL_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")

# Lucene query syntax special characters. User input is matched as plain
# terms, so these are stripped rather than escaped — escaping keeps them
# significant to the analyzer, stripping cannot produce a parse error.
_LUCENE_SPECIALS_RE = re.compile(r'[+\-&|!(){}\[\]^"~*?:\\/]')


def sanitize_lucene_query(text: str) -> str:
    """Reduce free text to plain Lucene terms safe for db.index.fulltext.queryNodes.

    Returns an empty string when nothing searchable remains; callers should
    skip the fulltext query in that case.
    """
    cleaned = _LUCENE_SPECIALS_RE.sub(" ", text)
    return " ".join(cleaned.split())


def normalize_domains(value: "str | Sequence[str]") -> list[str]:
    """Flatten a str or sequence of domain strings into a deduped, stripped list.

    Each element may itself be comma-separated. Order is preserved.
    Raises ValueError if the result is empty.
    """
    raw: list[str] = [value] if isinstance(value, str) else list(value)
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        for part in str(item).split(","):
            d = part.strip()
            if d and d not in seen:
                seen.add(d)
                out.append(d)
    if not out:
        raise ValueError("At least one --domain is required")
    return out


def domain_predicate(var: str, param: str = "domains") -> str:
    """Cypher WHERE fragment scoping `var` to any of the domains in $param.

    A one-element list is semantically identical to the old single-domain
    predicate (exact match OR sub-domain rollup via STARTS WITH).
    """
    return (
        f"({var}.domain IN ${param} "
        f"OR any(d IN ${param} WHERE {var}.domain STARTS WITH (d + '.')))"
    )


def asof_predicate(var: str, param: str = "asOf") -> str:
    """NULL-safe valid-time filter. Untimed nodes are always visible.

    Emitted only when the caller requests as-of filtering.
    """
    return (
        f"(${param} IS NULL OR "
        f"(({var}.valid_from IS NULL OR {var}.valid_from <= ${param}) "
        f"AND ({var}.valid_to IS NULL OR {var}.valid_to > ${param})))"
    )


PATTERN_REQUIRED_OPTIONS = {
    "pattern1": ("entityClass",),
    "pattern2": ("entityNameList",),
    "pattern3": ("entityNameList",),
    "pattern4": ("entityClass", "entityName"),
    "pattern5": ("entityClass1", "entityClass2", "entityName1", "entityName2"),
    "pattern6": ("entityName1", "entityName2"),
    "pattern7": ("searchTerm",),
    "pattern8": ("entityClass", "entityName"),
    "pattern9": ("entityClass",),
    "pattern10": ("documentName",),
}

# Name-based options that can be satisfied by an exact-id option instead.
# When both are supplied the id wins — it pins the node precisely after
# entity resolution, where CONTAINS matching could fan out to lookalikes.
PATTERN_OPTION_ALTERNATIVES = {
    "entityName": "entityId",
    "entityName1": "entityId1",
    "entityName2": "entityId2",
    "entityNameList": "entityIdList",
}


def normalize_entity_class(value: str) -> str:
    """Normalize a user-supplied entity class to the label shape ingestion writes."""
    normalized = re.sub(r"[^A-Za-z0-9_]", "_", value.strip()).upper()
    if not normalized:
        raise ValueError("Entity class cannot be empty")
    validate_label(normalized)
    return normalized


def validate_label(label: str) -> None:
    if not LABEL_RE.match(label):
        raise ValueError(f"Invalid Neo4j label: {label!r}")


def _connection_settings() -> dict[str, str]:
    env = load_env()
    return {
        "uri": env.get("ARTMIND_KG_NEO4J_URI", "neo4j://127.0.0.1:7687"),
        "user": env.get("ARTMIND_KG_NEO4J_USERNAME", "neo4j"),
        "password": env.get("ARTMIND_KG_NEO4J_PASSWORD", ""),
        "database": env.get("ARTMIND_KG_NEO4J_DATABASE", "neo4j"),
    }


@contextmanager
def neo4j_session():
    settings = _connection_settings()
    driver = GraphDatabase.driver(
        settings["uri"], auth=(settings["user"], settings["password"])
    )
    try:
        with driver.session(database=settings["database"]) as session:
            yield session
    finally:
        driver.close()


def strip_embeddings(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: strip_embeddings(val)
            for key, val in value.items()
            if key.lower() != "embedding"
        }
    if isinstance(value, list):
        return [strip_embeddings(item) for item in value]
    return value


def serialize_value(value: Any) -> Any:
    if isinstance(value, Node):
        return strip_embeddings(
            {
                "id": value.element_id,
                "labels": list(value.labels),
                "properties": dict(value),
            }
        )
    if isinstance(value, Relationship):
        return strip_embeddings(
            {
                "id": value.element_id,
                "type": value.type,
                "start_node_id": value.start_node.element_id,
                "end_node_id": value.end_node.element_id,
                "properties": dict(value),
            }
        )
    if isinstance(value, Path):
        return {
            "nodes": [serialize_value(node) for node in value.nodes],
            "relationships": [serialize_value(rel) for rel in value.relationships],
        }
    if isinstance(value, dict):
        return strip_embeddings({str(k): serialize_value(v) for k, v in value.items()})
    if isinstance(value, (list, tuple)):
        return [serialize_value(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def serialize_record(record: Any) -> dict:
    if hasattr(record, "data"):
        return strip_embeddings(serialize_value(record.data()))
    if isinstance(record, dict):
        return strip_embeddings(serialize_value(record))
    return strip_embeddings(serialize_value(dict(record)))


def _run_read_query(cypher: str, parameters: dict) -> list[dict]:
    with neo4j_session() as session:
        return [serialize_record(record) for record in session.run(cypher, **parameters)]


def _domain_output(domains: list[str]) -> dict:
    """Back-compat output keys: always 'domains'; add 'domain' when exactly one."""
    out: dict = {"domains": domains}
    if len(domains) == 1:
        out["domain"] = domains[0]
    return out


def graph_metadata(domains: "str | Sequence[str]", as_of: str | None = None) -> dict:
    domains = normalize_domains(domains)
    asof_node = f"\n        AND {asof_predicate('n')}" if as_of else ""
    cypher = f"""
    CALL () {{
      MATCH (n)
      WHERE {domain_predicate("n")}{asof_node}
      UNWIND labels(n) AS label
      WITH label, keys(n) AS nodeKeys, n.type AS typeVal
      UNWIND nodeKeys AS propName
      RETURN "nodes" AS category,
             label AS name,
             collect(DISTINCT propName) AS propertyNames,
             collect(DISTINCT typeVal) AS distinctTypes,
             null AS connections
    UNION
      MATCH (s)-[r]->(e)
      WHERE {domain_predicate("s")}
        AND {domain_predicate("e")}
      WITH type(r) AS relType, labels(s) AS fromLabels, labels(e) AS toLabels, keys(r) AS relKeys
      UNWIND relKeys AS propName
      RETURN "relationships" AS category,
             relType AS name,
             collect(DISTINCT propName) AS propertyNames,
             null AS distinctTypes,
             collect(DISTINCT {{from: fromLabels, to: toLabels}}) AS connections
    }}
    RETURN category, name, propertyNames, distinctTypes, connections
    ORDER BY category, name
    """
    return {
        **_domain_output(domains),
        "query_type": "graph",
        "command": "metadata",
        "rows": _run_read_query(cypher, {"domains": domains, **({"asOf": as_of} if as_of else {})}),
    }


def structural_metadata(domains: "str | Sequence[str]") -> dict:
    """Return focused metadata about Document, DocChunk, UserChat, and Entity nodes.

    Unlike graph_metadata() which returns the full schema, this returns only the
    structural node types and relationships with counts and Document names — compact
    enough for agents and text2cypher prompts to parse quickly.
    """
    domains = normalize_domains(domains)
    cypher = f"""
    CALL () {{
      MATCH (d:Document)
      WHERE {domain_predicate("d")}
      WITH count(d) AS cnt, collect(DISTINCT d.name) AS names
      RETURN 'Document' AS label, cnt AS count, names AS names, null AS relationship, null AS from_label, null AS to_label
    UNION
      MATCH (c:DocChunk)
      WHERE {domain_predicate("c")}
      WITH count(c) AS cnt
      RETURN 'DocChunk' AS label, cnt AS count, null AS names, null AS relationship, null AS from_label, null AS to_label
    UNION
      MATCH (u:UserChat)
      WHERE {domain_predicate("u")}
      WITH count(u) AS cnt
      RETURN 'UserChat' AS label, cnt AS count, null AS names, null AS relationship, null AS from_label, null AS to_label
    UNION
      MATCH (e:Entity)
      WHERE {domain_predicate("e")}
      WITH count(e) AS cnt
      RETURN 'Entity' AS label, cnt AS count, null AS names, null AS relationship, null AS from_label, null AS to_label
    UNION
      MATCH (c:DocChunk)-[r:PART_OF]->(d:Document)
      WHERE {domain_predicate("c")}
      WITH count(r) AS cnt
      RETURN null AS label, cnt AS count, null AS names, 'PART_OF' AS relationship, 'DocChunk' AS from_label, 'Document' AS to_label
    UNION
      MATCH (e:Entity)-[r:EXTRACTED_FROM]->(c:DocChunk)
      WHERE {domain_predicate("e")}
      WITH count(r) AS cnt
      RETURN null AS label, cnt AS count, null AS names, 'EXTRACTED_FROM' AS relationship, 'Entity' AS from_label, 'DocChunk' AS to_label
    UNION
      MATCH (c:DocChunk)-[r:MENTIONS]->(e:Entity)
      WHERE {domain_predicate("c")}
      WITH count(r) AS cnt
      RETURN null AS label, cnt AS count, null AS names, 'MENTIONS' AS relationship, 'DocChunk' AS from_label, 'Entity' AS to_label
    UNION
      MATCH (u:UserChat)-[r:MENTIONS]->(e:Entity)
      WHERE {domain_predicate("u")}
      WITH count(r) AS cnt
      RETURN null AS label, cnt AS count, null AS names, 'MENTIONS' AS relationship, 'UserChat' AS from_label, 'Entity' AS to_label
    }}
    RETURN label, count, names, relationship, from_label, to_label
    """
    return {
        **_domain_output(domains),
        "query_type": "graph",
        "command": "structural_metadata",
        "rows": _run_read_query(cypher, {"domains": domains}),
    }


def entity_listing(
    domains: "str | Sequence[str]",
    name_filter: str | None = None,
    count_all: bool = False,
    as_of: str | None = None,
) -> dict:
    domains = normalize_domains(domains)
    asof_node = f"\n      AND {asof_predicate('n')}" if as_of else ""
    cypher = f"""
    MATCH (n:Entity)
    WHERE {domain_predicate("n")} AND n.name IS NOT NULL
      AND ($nameFilter IS NULL OR toLower(n.name) CONTAINS toLower($nameFilter)){asof_node}
    UNWIND labels(n) AS label
    WITH label, n.type AS type, collect(DISTINCT n.name) AS names
    RETURN label, collect({{type: type, names: names}}) AS typeGroups
    ORDER BY label
    """
    result: dict = {
        **_domain_output(domains),
        "query_type": "graph",
        "command": "entity_listing",
        "rows": _run_read_query(
            cypher,
            {
                "domains": domains,
                "nameFilter": name_filter,
                **({"asOf": as_of} if as_of else {}),
            },
        ),
    }
    if name_filter is not None:
        result["name_filter"] = name_filter
    if count_all:
        count_cypher = f"""
        MATCH (n:Entity)
        WHERE {domain_predicate("n")} AND n.name IS NOT NULL{asof_node}
        RETURN count(DISTINCT n) AS total
        """
        count_rows = _run_read_query(
            count_cypher, {"domains": domains, **({"asOf": as_of} if as_of else {})}
        )
        result["total_entities"] = count_rows[0]["total"] if count_rows else 0
    return result


def validate_pattern_parameters(pattern: str, parameters: dict) -> None:
    if pattern not in PATTERN_REQUIRED_OPTIONS:
        raise ValueError(f"Unsupported graph query pattern: {pattern}")
    missing = [
        option
        for option in PATTERN_REQUIRED_OPTIONS[pattern]
        if not parameters.get(option)
        and not parameters.get(PATTERN_OPTION_ALTERNATIVES.get(option, ""))
    ]
    if missing:

        def _describe(name: str) -> str:
            alt = PATTERN_OPTION_ALTERNATIVES.get(name)
            return f"--{name} (or --{alt})" if alt else f"--{name}"

        raise ValueError(
            f"Missing required option(s) for {pattern}: "
            + ", ".join(_describe(name) for name in missing)
        )
    if parameters.get("mode") not in {None, "shortest", "all"}:
        raise ValueError("--mode must be 'shortest' or 'all'")
    if parameters.get("degreeMode") not in {None, "relations", "mentions", "all"}:
        raise ValueError("--degreeMode must be 'relations', 'mentions', or 'all'")


def normalize_pattern_parameters(pattern: str, parameters: dict) -> dict:
    params = {key: value for key, value in parameters.items() if value not in (None, ())}
    for key in ("entityClass", "entityClass1", "entityClass2"):
        if key in params:
            params[key] = normalize_entity_class(params[key])
    if "entityNameList" in params:
        params["entityNameList"] = list(params["entityNameList"])
    if "entityIdList" in params:
        params["entityIdList"] = list(params["entityIdList"])
    if "topN" in params:
        params["topN"] = int(params["topN"])
    if "limit" in params:
        params["limit"] = int(params["limit"])
    params.setdefault("mode", "shortest")
    return params


def _entity_selector(
    parameters: dict,
    cypher_params: dict,
    var: str,
    name_key: str = "entityName",
    id_key: str = "entityId",
) -> str:
    """WHERE fragment selecting an entity by exact id (preferred) or fuzzy name."""
    if parameters.get(id_key):
        cypher_params[id_key] = parameters[id_key]
        return f"{var}.id = ${id_key}"
    cypher_params[name_key] = parameters[name_key]
    return f"toLower({var}.name) CONTAINS toLower(${name_key})"


def _entity_list_selector(parameters: dict, cypher_params: dict, var: str) -> str:
    """WHERE fragment selecting entities by exact ids (preferred) or fuzzy names."""
    if parameters.get("entityIdList"):
        cypher_params["entityIdList"] = parameters["entityIdList"]
        return f"{var}.id IN $entityIdList"
    cypher_params["entityNameList"] = parameters["entityNameList"]
    return f"ANY(n IN $entityNameList WHERE toLower({var}.name) CONTAINS toLower(n))"


def _pattern_query(pattern: str, parameters: dict) -> tuple[str, dict]:
    as_of = parameters.get("asOf")

    def _asof(var: str) -> str:
        return f"\n              AND {asof_predicate(var)}" if as_of else ""

    if pattern == "pattern1":
        label = parameters["entityClass"]
        return (
            f"""
            MATCH (e:{label})
            WHERE {domain_predicate("e")}{_asof("e")}
            RETURN e {{.*, label: labels(e)}} AS entityData
            ORDER BY e.name
            LIMIT $limit
            """,
            {
                "domains": parameters["domains"],
                "limit": parameters.get("limit", 200),
                **({"asOf": as_of} if as_of else {}),
            },
        )
    if pattern == "pattern2":
        cypher_params = {"domains": parameters["domains"]}
        selector = _entity_list_selector(parameters, cypher_params, "e")
        if as_of:
            cypher_params["asOf"] = as_of
        return (
            f"""
            MATCH (e:Entity)
            WHERE {domain_predicate("e")}
              AND {selector}{_asof("e")}
            OPTIONAL MATCH (chunk:DocChunk)-[:MENTIONS]->(e)
            WITH e, collect(DISTINCT chunk {{ .id, .name, .doc_id, .domain, source_type: 'document' }}) AS doc_sources
            OPTIONAL MATCH (chat:UserChat)-[:MENTIONS]->(e)
            RETURN e {{.*, label: labels(e)}} AS entityData,
                   doc_sources,
                   collect(DISTINCT chat {{ .id, .session_id, .created_by, .created_at, source_type: 'user_chat' }}) AS chat_sources
            ORDER BY entityData.name
            """,
            cypher_params,
        )
    if pattern == "pattern3":
        cypher_params = {"domains": parameters["domains"]}
        selector = _entity_list_selector(parameters, cypher_params, "e")
        if as_of:
            cypher_params["asOf"] = as_of
        return (
            f"""
            MATCH (e:Entity)
            WHERE {domain_predicate("e")}
              AND {selector}{_asof("e")}
            OPTIONAL MATCH (e)-[r]-(t:Entity)
            WHERE {domain_predicate("t")}
            WITH e, collect(CASE WHEN r IS NULL THEN NULL ELSE {{
              type: type(r),
              properties: properties(r),
              target: {{name: t.name, label: labels(t)}}
            }} END) AS connections
            OPTIONAL MATCH (chunk:DocChunk)-[:MENTIONS]->(e)
            WITH e, connections, collect(DISTINCT chunk {{ .id, .name, .doc_id, .domain, source_type: 'document' }}) AS doc_sources
            OPTIONAL MATCH (chat:UserChat)-[:MENTIONS]->(e)
            RETURN properties(e) AS entityData, connections, doc_sources,
                   collect(DISTINCT chat {{ .id, .session_id, .created_by, .created_at, source_type: 'user_chat' }}) AS chat_sources
            ORDER BY entityData.name
            """,
            cypher_params,
        )
    if pattern == "pattern4":
        label = parameters["entityClass"]
        cypher_params = {"domains": parameters["domains"]}
        selector = _entity_selector(parameters, cypher_params, "e")
        if as_of:
            cypher_params["asOf"] = as_of
        return (
            f"""
            MATCH (e:{label})
            WHERE {domain_predicate("e")}
              AND {selector}{_asof("e")}
            OPTIONAL MATCH (e)-[r]-(t:Entity)
            WHERE {domain_predicate("t")}
            WITH e, collect(CASE WHEN r IS NULL THEN NULL ELSE {{
              rel_type: type(r),
              rel_properties: properties(r),
              connected_to: {{label: labels(t), data: properties(t)}}
            }} END) AS connections
            OPTIONAL MATCH (chunk:DocChunk)-[:MENTIONS]->(e)
            WITH e, connections, collect(DISTINCT chunk {{ .id, .name, .doc_id, .domain, source_type: 'document' }}) AS doc_sources
            OPTIONAL MATCH (chat:UserChat)-[:MENTIONS]->(e)
            RETURN properties(e) AS entityData, connections, doc_sources,
                   collect(DISTINCT chat {{ .id, .session_id, .created_by, .created_at, source_type: 'user_chat' }}) AS chat_sources
            ORDER BY entityData.name
            """,
            cypher_params,
        )
    if pattern == "pattern5":
        # No _asof() here (deliberate, unlike pattern6/8): the path can traverse
        # an arbitrary number of intermediate entities that aren't bound to a
        # variable name, so there's no single filter point that would currency-
        # scope the whole path without rewriting the traversal itself. Endpoint-
        # only filtering (e, t) would be misleading — it wouldn't stop the path
        # from routing through an intermediate entity that isn't valid as-of the
        # requested date.
        label1 = parameters["entityClass1"]
        label2 = parameters["entityClass2"]
        cypher_params = {"domains": parameters["domains"]}
        selector1 = _entity_selector(parameters, cypher_params, "e", "entityName1", "entityId1")
        selector2 = _entity_selector(parameters, cypher_params, "t", "entityName2", "entityId2")
        # Flatten to a genuinely interleaved [node, rel, node, rel, ..., node]
        # list via reduce — a plain list comprehension here would nest each
        # [node, rel] pair as its own sub-list instead of flattening it.
        interleave = """
                  reduce(acc = [{label: labels(nodes(p)[0]), data: properties(nodes(p)[0])}],
                    i IN range(0, length(p)-1) |
                    acc + [
                      {rel: type(relationships(p)[i]), data: properties(relationships(p)[i])},
                      {label: labels(nodes(p)[i+1]), data: properties(nodes(p)[i+1])}
                    ]
                  ) AS interleavedPath"""
        if parameters["mode"] == "all":
            return (
                f"""
                MATCH (e:{label1}), (t:{label2})
                WHERE {domain_predicate("e")}
                  AND {domain_predicate("t")}
                  AND {selector1}
                  AND {selector2}
                WITH e, t
                MATCH p = (e)-[*1..5]-(t)
                WHERE all(x IN nodes(p) WHERE x:Entity)
                WITH p
                ORDER BY length(p) ASC
                LIMIT 3
                RETURN {interleave}
                """,
                cypher_params,
            )
        return (
            f"""
            MATCH p = shortestPath((e:{label1})-[*..5]-(t:{label2}))
            WHERE {domain_predicate("e")}
              AND {domain_predicate("t")}
              AND {selector1}
              AND {selector2}
              AND all(x IN nodes(p) WHERE x:Entity)
            RETURN {interleave}
            """,
            cypher_params,
        )
    if pattern == "pattern6":
        cypher_params = {"domains": parameters["domains"]}
        selector1 = _entity_selector(parameters, cypher_params, "e1", "entityName1", "entityId1")
        selector2 = _entity_selector(parameters, cypher_params, "e2", "entityName2", "entityId2")
        if as_of:
            cypher_params["asOf"] = as_of
        return (
            f"""
            MATCH (e1:Entity)-[r]-(e2:Entity)
            WHERE {domain_predicate("e1")}
              AND {domain_predicate("e2")}
              AND {selector1}
              AND {selector2}{_asof("e1")}{_asof("e2")}
            RETURN type(r) AS relType,
                   properties(r) AS relProps,
                   startNode(r).name AS fromEntity,
                   endNode(r).name AS toEntity,
                   labels(startNode(r)) AS fromLabels,
                   labels(endNode(r)) AS toLabels
            ORDER BY relType, fromEntity, toEntity
            """,
            cypher_params,
        )
    if pattern == "pattern7":
        search_term = sanitize_lucene_query(parameters["searchTerm"])
        if not search_term:
            raise ValueError("--searchTerm contains no searchable text")
        return (
            f"""
            CALL db.index.fulltext.queryNodes('entity_name_ft', $searchTerm)
            YIELD node AS e, score AS ftScore
            WHERE {domain_predicate("e")}{_asof("e")}
            RETURN e {{.*, label: labels(e)}} AS entityData
            ORDER BY ftScore DESC, e.name
            LIMIT $limit
            """,
            {
                "domains": parameters["domains"],
                "searchTerm": search_term,
                "limit": parameters.get("limit", 10),
                **({"asOf": as_of} if as_of else {}),
            },
        )
    if pattern == "pattern8":
        label = parameters["entityClass"]
        cypher_params = {"domains": parameters["domains"]}
        selector = _entity_selector(parameters, cypher_params, "t")
        if as_of:
            cypher_params["asOf"] = as_of
        return (
            f"""
            MATCH (e:{label})-[r]-(t:Entity)
            WHERE {domain_predicate("e")}
              AND {domain_predicate("t")}
              AND {selector}{_asof("e")}{_asof("t")}
            RETURN e {{.*, label: labels(e)}} AS entityData,
                   type(r) AS relType,
                   properties(r) AS relProps
            ORDER BY e.name, relType
            """,
            cypher_params,
        )
    if pattern == "pattern9":
        label = parameters["entityClass"]
        # relations: entity-entity connectivity; mentions: how often sources
        # mention the entity; all: every edge including structural ones.
        degree_match = {
            "relations": "OPTIONAL MATCH (e)-[r]-(:Entity)",
            "mentions": "OPTIONAL MATCH (e)<-[r:MENTIONS]-()",
            "all": "OPTIONAL MATCH (e)-[r]-()",
        }[parameters.get("degreeMode", "relations")]
        return (
            f"""
            MATCH (e:{label})
            WHERE {domain_predicate("e")}{_asof("e")}
            {degree_match}
            WITH e, count(r) AS degree
            RETURN e {{.*, label: labels(e), degree: degree}} AS entityData
            ORDER BY degree DESC, e.name
            LIMIT $topN
            """,
            {
                "domains": parameters["domains"],
                "topN": parameters.get("topN", 5),
                **({"asOf": as_of} if as_of else {}),
            },
        )
    if pattern == "pattern10":
        # No _asof() here: pattern10's primary filter is on the Document (d),
        # but the projected temporal fields (valid_from/valid_to/superseded_by)
        # live on both Document and, more granularly, per-chunk currency isn't
        # modeled — filtering only on d while returning all its chunks would
        # silently mix "document is current" with "chunk content is current",
        # which is a bigger modeling decision than this task should make. Left
        # for a follow-up once chunk-level supersession is defined.
        return (
            f"""
            MATCH (c:DocChunk)-[:PART_OF]->(d:Document)
            WHERE {domain_predicate("d")}
              AND toLower(d.name) CONTAINS toLower($documentName)
            RETURN d {{ .id, .name, .path, .domain, .valid_from, .valid_to, .superseded_by }} AS document,
                   c {{ .id, .name, .doc_id, .domain, .valid_to, .text }} AS chunk
            ORDER BY c.name
            """,
            {"domains": parameters["domains"], "documentName": parameters["documentName"]},
        )
    raise ValueError(f"Unsupported graph query pattern: {pattern}")


def execute_pattern(
    domains: "str | Sequence[str]",
    pattern: str,
    question: str | None = None,
    as_of: str | None = None,
    **parameters,
) -> dict:
    domains = normalize_domains(domains)
    params = normalize_pattern_parameters(pattern, {"domains": domains, "asOf": as_of, **parameters})
    validate_pattern_parameters(pattern, params)
    cypher, cypher_params = _pattern_query(pattern, params)
    output_parameters = {
        key: value
        for key, value in params.items()
        if key != "domains" and value is not None
    }
    return {
        **_domain_output(domains),
        "query_type": "graph",
        "command": "pattern",
        "pattern": pattern,
        "question": question,
        "parameters": output_parameters,
        "rows": strip_embeddings(_run_read_query(cypher, cypher_params)),
    }


def domains_overview() -> dict:
    """One aggregation grouped by n.domain: doc names/counts, entity counts, top classes.

    The cheap routing input that maps an area ("banking") to concrete sibling domains.
    """
    cypher = """
    CALL () {
      MATCH (d:Document)
      RETURN d.domain AS domain, 'documents' AS k,
             count(d) AS c, collect(DISTINCT d.name)[0..25] AS names
    UNION
      MATCH (e:Entity)
      RETURN e.domain AS domain, 'entities' AS k, count(e) AS c, null AS names
    UNION
      MATCH (e:Entity)
      WITH e.domain AS domain, e.entity_class AS cls, count(*) AS n
      ORDER BY n DESC
      WITH domain, collect(cls)[0..8] AS top
      RETURN domain, 'top_classes' AS k, 0 AS c, top AS names
    }
    WITH domain, collect({k: k, c: c, names: names}) AS parts
    RETURN domain, parts
    ORDER BY domain
    """
    rows = _run_read_query(cypher, {})
    overview: dict = {}
    for row in rows:
        d = row["domain"]
        entry = overview.setdefault(d, {"domain": d})
        for p in row["parts"]:
            if p["k"] == "documents":
                entry["document_count"] = p["c"]
                entry["documents"] = p["names"]
            elif p["k"] == "entities":
                entry["entity_count"] = p["c"]
            elif p["k"] == "top_classes":
                entry["top_classes"] = p["names"]
    return {
        "query_type": "graph",
        "command": "domains_overview",
        "domains": sorted(overview.keys()),
        "rows": [overview[k] for k in sorted(overview.keys())],
    }


def list_conflicts(
    domains: "str | Sequence[str]",
    entity_ids: "Sequence[str] | None" = None,
    entity_name: str | None = None,
    status: str = "open",
) -> dict:
    """Read materialized Conflict nodes scoped to the given domains.

    status='all' returns every status; otherwise filters Conflict.status.
    """
    domains = normalize_domains(domains)
    entity_ids = list(entity_ids or [])
    # Conflict.domains is a list property (a conflict can span 2+ domains), unlike
    # Entity/Document/DocChunk's scalar .domain — so this scopes via list containment
    # instead of domain_predicate().
    cypher = f"""
    MATCH (co:Conflict)
    WHERE any(d IN $domains WHERE d IN co.domains)
      AND ($status = 'all' OR co.status = $status)
    OPTIONAL MATCH (co)-[:CONFLICT_OF]->(e:Entity)
    WITH co, collect(DISTINCT e {{ .id, .name, .entity_class, .domain }}) AS entities
    WHERE ($entityIds = [] OR any(e IN entities WHERE e.id IN $entityIds))
      AND ($entityName IS NULL OR any(e IN entities WHERE toLower(e.name) CONTAINS toLower($entityName)))
    OPTIONAL MATCH (co)-[ev:EVIDENCE]->(c:DocChunk)
    RETURN co {{ .* }} AS conflict, entities,
           collect(DISTINCT {{ side: ev.side, chunk_id: c.id, doc_id: c.doc_id, domain: c.domain, text: c.text }}) AS evidence
    ORDER BY conflict.severity DESC, conflict.detected_at DESC
    """
    return {
        **_domain_output(domains),
        "query_type": "graph",
        "command": "conflicts",
        "status": status,
        "rows": _run_read_query(
            cypher,
            {"domains": domains, "entityIds": entity_ids, "entityName": entity_name, "status": status},
        ),
    }
