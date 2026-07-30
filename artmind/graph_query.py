import re
from contextlib import contextmanager
from datetime import date
from typing import Any, Sequence

from neo4j import READ_ACCESS, GraphDatabase
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
        f"OR any(dom IN ${param} WHERE {var}.domain STARTS WITH (dom + '.')))"
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


# ISO date at year, month, or day precision (optionally with a time suffix).
# valid_from/valid_to are ISO strings compared lexically, so prefixes work.
_ASOF_RE = re.compile(r"^\d{4}(-\d{2}){0,2}(T[0-9:.+-]+)?$")


def resolve_as_of(value: str | None) -> str | None:
    """Resolve an --asOf value to an ISO date string.

    Accepts 'today'/'now' (resolved to the current date) and ISO dates at
    year/month/day precision. Anything else raises ValueError: asof_predicate
    compares strings lexically, so an unresolved value like the literal
    'today' would silently hide every node that carries a valid_to.
    """
    if value is None:
        return None
    v = value.strip()
    if not v:
        return None
    if v.lower() in ("today", "now"):
        return date.today().isoformat()
    if not _ASOF_RE.match(v):
        raise ValueError(
            f"--asOf must be an ISO date (YYYY[-MM[-DD]]) or 'today'/'now'; got {value!r}"
        )
    return v


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
def neo4j_session(access_mode: str | None = None):
    settings = _connection_settings()
    driver = GraphDatabase.driver(
        settings["uri"], auth=(settings["user"], settings["password"])
    )
    try:
        session_kwargs: dict = {"database": settings["database"]}
        if access_mode is not None:
            session_kwargs["default_access_mode"] = access_mode
        with driver.session(**session_kwargs) as session:
            yield session
    finally:
        driver.close()


def read_session():
    """A session the server enforces as read-only.

    All query-layer retrieval goes through this — it is the hard guarantee
    behind text2cypher's regex write-check, which is only a friendly pre-check.
    """
    return neo4j_session(access_mode=READ_ACCESS)


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
    with read_session() as session:
        return [serialize_record(record) for record in session.run(cypher, **parameters)]


def _domain_output(domains: list[str]) -> dict:
    """Back-compat output keys: always 'domains'; add 'domain' when exactly one."""
    out: dict = {"domains": domains}
    if len(domains) == 1:
        out["domain"] = domains[0]
    return out


def graph_metadata(domains: "str | Sequence[str]", as_of: str | None = None) -> dict:
    domains = normalize_domains(domains)
    as_of = resolve_as_of(as_of)
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
    as_of = resolve_as_of(as_of)
    asof_node = f"\n      AND {asof_predicate('n')}" if as_of else ""
    cypher = f"""
    MATCH (n:Entity)
    WHERE {domain_predicate("n")} AND n.name IS NOT NULL
      AND ($nameFilter IS NULL OR toLower(n.name) CONTAINS toLower($nameFilter)){asof_node}
    UNWIND [l IN labels(n) WHERE l <> 'Entity'] AS label
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
            OPTIONAL MATCH (e)-[:EXTRACTED_FROM]->(chunk:DocChunk)
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
            OPTIONAL MATCH (e)-[:EXTRACTED_FROM]->(chunk:DocChunk)
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
            OPTIONAL MATCH (e)-[:EXTRACTED_FROM]->(chunk:DocChunk)
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
                // Pin one deterministic endpoint pair before enumerating paths:
                // name selectors can fan out to many (e, t) pairs, and all-paths
                // enumeration to depth 5 is exponential per pair. With exact ids
                // (the resolution protocol's normal case) this is a no-op.
                WITH e, t
                ORDER BY e.id, t.id
                LIMIT 1
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
        degree_mode = parameters.get("degreeMode", "relations")
        if degree_mode == "mentions":
            # mentions: how often sources mention the entity. Document mentions are
            # (Entity)-[:EXTRACTED_FROM]->(DocChunk) (the only edge ingestion writes
            # between the two); chat mentions are (UserChat)-[:MENTIONS]->(Entity).
            # :MENTIONS is never written for DocChunk, so it must not be queried here.
            degree_body = """
            OPTIONAL MATCH (e)-[r1:EXTRACTED_FROM]->(:DocChunk)
            OPTIONAL MATCH (e)<-[r2:MENTIONS]-(:UserChat)
            WITH e, count(DISTINCT r1) + count(DISTINCT r2) AS degree
            """
        else:
            # relations: entity-entity connectivity (neighbor domain-scoped like
            # every other entity match); all: every edge including structural ones.
            degree_match = {
                "relations": f'OPTIONAL MATCH (e)-[r]-(t:Entity) WHERE {domain_predicate("t")}',
                "all": "OPTIONAL MATCH (e)-[r]-()",
            }[degree_mode]
            degree_body = f"""
            {degree_match}
            WITH e, count(r) AS degree
            """
        return (
            f"""
            MATCH (e:{label})
            WHERE {domain_predicate("e")}{_asof("e")}
            {degree_body}
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
            ORDER BY c.id
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
    as_of = resolve_as_of(as_of)
    params = normalize_pattern_parameters(pattern, {"domains": domains, "asOf": as_of, **parameters})
    validate_pattern_parameters(pattern, params)
    cypher, cypher_params = _pattern_query(pattern, params)
    output_parameters = {
        key: value
        for key, value in params.items()
        if key != "domains" and value is not None
    }
    result = {
        **_domain_output(domains),
        "query_type": "graph",
        "command": "pattern",
        "pattern": pattern,
        "question": question,
        "parameters": output_parameters,
        "rows": strip_embeddings(_run_read_query(cypher, cypher_params)),
    }
    # pattern5/pattern10 deliberately skip valid-time filtering (see the
    # comments in _pattern_query) — say so instead of silently accepting it.
    if as_of and pattern in ("pattern5", "pattern10"):
        result["asOf_ignored"] = True
    return result


def _chunks_query(expand: int, as_of: str | None) -> str:
    """Cypher for chunks_by_id. expand > 0 adds a same-document neighbor window."""
    asof_c = f"\n      AND {asof_predicate('c')}" if as_of else ""
    neighbor_call = ""
    neighbor_return = ""
    if expand > 0:
        # Chunk sequence is encoded in the id ({doc_id}_{seq:03d}, zero-padded
        # so lexical order == reading order; names like "Chunk 16/38" do NOT
        # sort correctly). The ±N window is computed over the document's
        # chunks sorted by id.
        neighbor_call = """
    CALL (c) {
      MATCH (s:DocChunk {doc_id: c.doc_id})
      WITH c, s
      ORDER BY s.id
      WITH c, collect(s) AS sibs
      WITH sibs, coalesce([i IN range(0, size(sibs)-1) WHERE sibs[i].id = c.id][0], -1) AS idx
      RETURN [j IN range(idx - $expand, idx + $expand)
              WHERE idx >= 0 AND j >= 0 AND j < size(sibs) AND j <> idx
              | {id: sibs[j].id, name: sibs[j].name, valid_to: sibs[j].valid_to, text: sibs[j].text}] AS neighbors
    }"""
        neighbor_return = ",\n           neighbors"
    return f"""
    MATCH (c:DocChunk)
    WHERE c.id IN $chunkIds
      AND {domain_predicate("c")}{asof_c}
    OPTIONAL MATCH (c)-[:PART_OF]->(d:Document){neighbor_call}
    RETURN c {{ .id, .name, .doc_id, .domain, .valid_from, .valid_to, .text }} AS chunk,
           d {{ .id, .name, .path, .domain, .valid_from, .valid_to, .superseded_by }} AS document{neighbor_return}
    ORDER BY c.id
    """


def chunks_by_id(
    domains: "str | Sequence[str]",
    chunk_ids: Sequence[str],
    expand: int = 0,
    as_of: str | None = None,
) -> dict:
    """Fetch chunk text by exact id — the deterministic grounding step for the
    chunk ids that patterns 2/3/4 return as doc_sources and conflicts return
    as evidence. expand=N also returns up to N adjacent chunks per hit from
    the same document."""
    domains = normalize_domains(domains)
    as_of = resolve_as_of(as_of)
    ids = [str(c).strip() for c in chunk_ids if str(c).strip()]
    if not ids:
        raise ValueError("At least one chunk id is required (--idList)")
    expand = int(expand)
    if expand < 0:
        raise ValueError("--expand must be >= 0")
    cypher = _chunks_query(expand, as_of)
    params = {
        "domains": domains,
        "chunkIds": ids,
        **({"expand": expand} if expand > 0 else {}),
        **({"asOf": as_of} if as_of else {}),
    }
    return {
        **_domain_output(domains),
        "query_type": "graph",
        "command": "chunks",
        "parameters": {"chunkIds": ids, "expand": expand, **({"asOf": as_of} if as_of else {})},
        "rows": _run_read_query(cypher, params),
    }


def _entity_context_query(as_of: str | None) -> str:
    """Cypher for entity_context: entity + one-hop relationships + source text.

    asOf applies to the entity and its source chunks (document content
    currency); neighbor entities follow pattern4's semantics (unfiltered).
    Chunks are ordered current-first (valid_to IS NULL), then by name; the
    first $includeChunks are returned with text, the rest as ids only.
    """
    asof_e = f"\n      AND {asof_predicate('e')}" if as_of else ""
    asof_c = f"\n    WHERE {asof_predicate('c')}" if as_of else ""
    return f"""
    MATCH (e:Entity {{id: $entityId}})
    WHERE {domain_predicate("e")}{asof_e}
    OPTIONAL MATCH (e)-[r]-(t:Entity)
    WHERE {domain_predicate("t")}
    WITH e, collect(CASE WHEN r IS NULL THEN NULL ELSE {{
      type: type(r),
      properties: properties(r),
      target: {{id: t.id, name: t.name, label: labels(t)}}
    }} END) AS connections
    OPTIONAL MATCH (e)-[:EXTRACTED_FROM]->(c:DocChunk){asof_c}
    OPTIONAL MATCH (c)-[:PART_OF]->(d:Document)
    WITH e, connections, c, d
    ORDER BY c.valid_to IS NULL DESC, c.id
    WITH e, connections, [x IN collect(CASE WHEN c IS NULL THEN NULL ELSE c {{
      .id, .name, .doc_id, .domain, .valid_to, .text,
      document: d {{ .id, .name, .domain, .valid_from, .valid_to, .superseded_by }}
    }} END) WHERE x IS NOT NULL] AS allChunks
    OPTIONAL MATCH (chat:UserChat)-[:MENTIONS]->(e)
    WITH e, connections, allChunks,
         collect(DISTINCT chat {{ .id, .raw_text, .session_id, .created_by, .created_at }}) AS chat_sources
    RETURN e {{.*, label: labels(e)}} AS entityData,
           [x IN connections WHERE x IS NOT NULL] AS connections,
           allChunks[0..$includeChunks] AS chunks,
           [x IN allChunks[$includeChunks..] | {{id: x.id, name: x.name, doc_id: x.doc_id}}] AS more_chunks,
           chat_sources
    """


def entity_context(
    domains: "str | Sequence[str]",
    entity_id: str,
    include_chunks: int = 5,
    as_of: str | None = None,
) -> dict:
    """One-call grounded picture of a resolved entity: properties, one-hop
    relationships, and the text of its most current source chunks. Replaces
    the pattern4 + chunk-fetch sequence for entity-anchored questions."""
    domains = normalize_domains(domains)
    as_of = resolve_as_of(as_of)
    if not (entity_id or "").strip():
        raise ValueError("--entityId is required")
    include_chunks = int(include_chunks)
    if include_chunks < 0:
        raise ValueError("--includeChunks must be >= 0")
    cypher = _entity_context_query(as_of)
    params = {
        "domains": domains,
        "entityId": entity_id.strip(),
        "includeChunks": include_chunks,
        **({"asOf": as_of} if as_of else {}),
    }
    return {
        **_domain_output(domains),
        "query_type": "graph",
        "command": "entity_context",
        "parameters": {
            "entityId": entity_id.strip(),
            "includeChunks": include_chunks,
            **({"asOf": as_of} if as_of else {}),
        },
        "rows": strip_embeddings(_run_read_query(cypher, params)),
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

    # Union in domains that hold structured tables. Without this the aggregation
    # above only ever reports domains with Document/Entity nodes, so a corpus
    # whose tables sit at a coarser root than its documents (banking.cases,
    # banking.policy, ... for documents; bare banking for tables) never surfaces
    # the table-bearing domain at all -- and the routing workflow that starts
    # here can't discover a structured store that plainly exists.
    try:
        from artmind.structured import registry as structured_registry

        for table in structured_registry.list_tables():
            entry = overview.setdefault(table["domain"], {"domain": table["domain"]})
            entry["table_count"] = entry.get("table_count", 0) + 1
            entry.setdefault("tables", []).append(table["table_name"])
    except Exception:
        # A query-only host has no registry DB ($ARTMIND_DATA_DIR absent). The
        # graph half of this overview must still work there.
        pass

    for entry in overview.values():
        entry["stores"] = [
            name
            for name, present in (
                ("graph", bool(entry.get("document_count") or entry.get("entity_count"))),
                ("structured", bool(entry.get("table_count"))),
            )
            if present
        ]

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
    """List live conflicts, scoped to the given domains.

    Matches the bidirectional CONFLICTS_WITH shortcut edge directly (written by
    detect-conflicts' materialize() alongside the Conflict node, on both entities)
    rather than requiring the Conflict/CONFLICT_OF/EVIDENCE subgraph to still exist.
    This degrades gracefully: CONFLICTS_WITH edges carry their own conflict_id/aspect
    and remain queryable even if the Conflict node they were minted with has since been
    deleted (observed live on the banking-corpus graph — CONFLICTS_WITH edges survived,
    Conflict/EVIDENCE nodes did not) — the old Conflict-node-only query silently
    returned zero rows in that state. The Conflict node, when present, is joined in as
    optional enrichment (severity, claim_a/claim_b, detected_at) — see `materialized` on
    each row. status='all' returns every status; otherwise filters against
    coalesce(Conflict.status, 'open'), since an orphaned edge with no Conflict node has
    no recorded status and defaults to the same 'open' value materialize() sets on create.
    """
    domains = normalize_domains(domains)
    entity_ids = list(entity_ids or [])
    cypher = f"""
    MATCH (a:Entity)-[r:CONFLICTS_WITH]->(b:Entity)
    WHERE {domain_predicate("a")} AND {domain_predicate("b")} AND a.id < b.id
    WITH r.conflict_id AS conflictId, r.aspect AS aspect,
         collect(DISTINCT a {{ .id, .name, .entity_class, .domain }})
           + collect(DISTINCT b {{ .id, .name, .entity_class, .domain }}) AS entities
    WHERE ($entityIds = [] OR any(e IN entities WHERE e.id IN $entityIds))
      AND ($entityName IS NULL OR any(e IN entities WHERE toLower(e.name) CONTAINS toLower($entityName)))
    OPTIONAL MATCH (co:Conflict {{id: conflictId}})
    WITH conflictId, aspect, entities, co, coalesce(co.status, 'open') AS effectiveStatus
    WHERE $status = 'all' OR effectiveStatus = $status
    OPTIONAL MATCH (co)-[ev:EVIDENCE]->(c:DocChunk)
    WITH conflictId, aspect, entities, co, effectiveStatus,
         [x IN collect(CASE WHEN c IS NULL THEN NULL ELSE {{
           side: ev.side, chunk_id: c.id, doc_id: c.doc_id, domain: c.domain, text: c.text
         }} END) WHERE x IS NOT NULL] AS evidence
    RETURN {{
      id: conflictId, aspect: aspect, status: effectiveStatus,
      severity: co.severity, claim_a: co.claim_a, claim_b: co.claim_b,
      domains: co.domains, detected_at: co.detected_at, detected_by_model: co.detected_by_model,
      materialized: co IS NOT NULL
    }} AS conflict, entities, evidence
    ORDER BY co.severity DESC, co.detected_at DESC
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


def list_timeline(domains: "str | Sequence[str]", entity_id: str) -> dict:
    """An entity's events/state-changes/supersessions ordered by event_at/valid_from."""
    domains = normalize_domains(domains)
    cypher = f"""
    MATCH (e:Entity {{id:$entityId}})
    WHERE {domain_predicate("e")}
    OPTIONAL MATCH (e)-[r]-(rel:Entity)
    WITH e, collect(DISTINCT {{
        type: type(r), name: rel.name,
        event_at: rel.event_at, valid_from: rel.valid_from, valid_to: rel.valid_to
    }}) AS related
    RETURN e {{ .id, .name, .entity_class, .event_at, .valid_from, .valid_to }} AS entity,
           [x IN related WHERE x.event_at IS NOT NULL OR x.valid_from IS NOT NULL] AS timeline
    """
    rows = _run_read_query(cypher, {"domains": domains, "entityId": entity_id})
    for row in rows:
        row["timeline"] = sorted(
            row.get("timeline", []),
            key=lambda x: (x.get("event_at") or x.get("valid_from") or ""),
        )
    return {**_domain_output(domains), "query_type": "graph", "command": "timeline", "rows": rows}


def entity_versions(
    domains: "str | Sequence[str]",
    entity_id: str,
    as_of: str | None = None,
) -> dict:
    """An entity's prior states from the history zone.

    Without as_of, the full chain oldest-first. With as_of, only the snapshot
    whose validity window covers that date — the state in force then. An empty
    result with as_of set means no snapshot covers the date, so the live entity
    was already current: callers fall back to the entity itself.

    Deliberately a separate command rather than making pattern2 --asOf return
    historical values: that would silently change the meaning of the most-used
    pattern (see pattern10's asOf_ignored for the precedent this follows).
    """
    domains = normalize_domains(domains)
    as_of = resolve_as_of(as_of)
    asof_clause = ""
    params: dict = {"domains": domains, "entityId": entity_id}
    if as_of:
        # Not asof_predicate(): its NULL-safety is correct for live nodes (NULL
        # valid_to means "still open") but wrong here — temporal.py deliberately
        # leaves :EntityVersion.valid_to as None when the supersession date is
        # unknown, so NULL here means "closed, date unknown," not "still open."
        # Dropping the "valid_to IS NULL OR" disjunct lets NULL > $asOf evaluate
        # to NULL, which WHERE treats as not-satisfying — excluding it correctly.
        asof_clause = (
            "\n      AND (v.valid_from IS NULL OR v.valid_from <= $asOf)"
            "\n      AND v.valid_to > $asOf"
        )
        params["asOf"] = as_of
    cypher = f"""
    MATCH (v:EntityVersion)
    WHERE v.entity_id = $entityId
      AND {domain_predicate("v")}{asof_clause}
    OPTIONAL MATCH (e:Entity {{id: $entityId}})
    RETURN v AS version, e {{ .id, .name, .entity_class, .valid_from, .valid_to }} AS entity
    ORDER BY v.valid_from
    """
    return {
        **_domain_output(domains),
        "query_type": "graph",
        "command": "entity_versions",
        "entity_id": entity_id,
        **({"asOf": as_of} if as_of else {}),
        "rows": strip_embeddings(_run_read_query(cypher, params)),
    }
