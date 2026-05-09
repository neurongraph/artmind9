import re
from contextlib import contextmanager
from typing import Any

from neo4j import GraphDatabase
from neo4j.graph import Node, Path, Relationship

from utils.functions import load_env


LABEL_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")

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


def graph_metadata(domain: str) -> dict:
    cypher = """
    CALL () {
      MATCH (n)
      WHERE (n.domain = $domain OR n.domain STARTS WITH ($domain + '.'))
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
      WHERE (s.domain = $domain OR s.domain STARTS WITH ($domain + '.'))
        AND (e.domain = $domain OR e.domain STARTS WITH ($domain + '.'))
      WITH type(r) AS relType, labels(s) AS fromLabels, labels(e) AS toLabels, keys(r) AS relKeys
      UNWIND relKeys AS propName
      RETURN "relationships" AS category,
             relType AS name,
             collect(DISTINCT propName) AS propertyNames,
             null AS distinctTypes,
             collect(DISTINCT {from: fromLabels, to: toLabels}) AS connections
    }
    RETURN category, name, propertyNames, distinctTypes, connections
    ORDER BY category, name
    """
    return {
        "domain": domain,
        "query_type": "graph",
        "command": "metadata",
        "rows": _run_read_query(cypher, {"domain": domain}),
    }


def entity_listing(
    domain: str,
    name_filter: str | None = None,
    count_all: bool = False,
) -> dict:
    cypher = """
    MATCH (n:Entity)
    WHERE (n.domain = $domain OR n.domain STARTS WITH ($domain + '.')) AND n.name IS NOT NULL
      AND ($nameFilter IS NULL OR toLower(n.name) CONTAINS toLower($nameFilter))
    UNWIND labels(n) AS label
    WITH label, n.type AS type, collect(DISTINCT n.name) AS names
    RETURN label, collect({type: type, names: names}) AS typeGroups
    ORDER BY label
    """
    result: dict = {
        "domain": domain,
        "query_type": "graph",
        "command": "entity_listing",
        "rows": _run_read_query(cypher, {"domain": domain, "nameFilter": name_filter}),
    }
    if name_filter is not None:
        result["name_filter"] = name_filter
    if count_all:
        count_cypher = """
        MATCH (n:Entity)
        WHERE (n.domain = $domain OR n.domain STARTS WITH ($domain + '.')) AND n.name IS NOT NULL
        RETURN count(DISTINCT n) AS total
        """
        count_rows = _run_read_query(count_cypher, {"domain": domain})
        result["total_entities"] = count_rows[0]["total"] if count_rows else 0
    return result


def validate_pattern_parameters(pattern: str, parameters: dict) -> None:
    if pattern not in PATTERN_REQUIRED_OPTIONS:
        raise ValueError(f"Unsupported graph query pattern: {pattern}")
    missing = [
        option
        for option in PATTERN_REQUIRED_OPTIONS[pattern]
        if not parameters.get(option)
    ]
    if missing:
        raise ValueError(
            f"Missing required option(s) for {pattern}: "
            + ", ".join(f"--{name}" for name in missing)
        )
    if parameters.get("mode") not in {None, "shortest", "all"}:
        raise ValueError("--mode must be 'shortest' or 'all'")


def normalize_pattern_parameters(pattern: str, parameters: dict) -> dict:
    params = {key: value for key, value in parameters.items() if value not in (None, ())}
    for key in ("entityClass", "entityClass1", "entityClass2"):
        if key in params:
            params[key] = normalize_entity_class(params[key])
    if "entityNameList" in params:
        params["entityNameList"] = list(params["entityNameList"])
    if "topN" in params:
        params["topN"] = int(params["topN"])
    if "limit" in params:
        params["limit"] = int(params["limit"])
    params.setdefault("mode", "shortest")
    return params


def _pattern_query(pattern: str, parameters: dict) -> tuple[str, dict]:
    if pattern == "pattern1":
        label = parameters["entityClass"]
        return (
            f"""
            MATCH (e:{label})
            WHERE (e.domain = $domain OR e.domain STARTS WITH ($domain + '.'))
            RETURN e {{.*, label: labels(e)}} AS entityData
            ORDER BY e.name
            """,
            {"domain": parameters["domain"]},
        )
    if pattern == "pattern2":
        return (
            """
            MATCH (e)
            WHERE (e.domain = $domain OR e.domain STARTS WITH ($domain + '.'))
              AND ANY(n IN $entityNameList WHERE toLower(e.name) CONTAINS toLower(n))
            OPTIONAL MATCH (chunk:DocChunk)-[:MENTIONS]->(e)
            OPTIONAL MATCH (chat:UserChat)-[:MENTIONS]->(e)
            RETURN e {.*, label: labels(e)} AS entityData,
                   collect(DISTINCT chunk { .id, .name, .doc_id, source_type: 'document' }) AS doc_sources,
                   collect(DISTINCT chat { .id, .session_id, .created_by, .created_at, source_type: 'user_chat' }) AS chat_sources
            ORDER BY entityData.name
            """,
            {
                "domain": parameters["domain"],
                "entityNameList": parameters["entityNameList"],
            },
        )
    if pattern == "pattern3":
        return (
            """
            MATCH (e)
            WHERE (e.domain = $domain OR e.domain STARTS WITH ($domain + '.'))
              AND ANY(n IN $entityNameList WHERE toLower(e.name) CONTAINS toLower(n))
            OPTIONAL MATCH (e)-[r]-(t)
            WHERE (t.domain = $domain OR t.domain STARTS WITH ($domain + '.'))
            WITH e, collect({
              type: type(r),
              properties: properties(r),
              target: {name: t.name, label: labels(t)}
            }) AS connections
            OPTIONAL MATCH (chunk:DocChunk)-[:MENTIONS]->(e)
            OPTIONAL MATCH (chat:UserChat)-[:MENTIONS]->(e)
            RETURN properties(e) AS entityData, connections,
                   collect(DISTINCT chunk { .id, .name, .doc_id, source_type: 'document' }) AS doc_sources,
                   collect(DISTINCT chat { .id, .session_id, .created_by, .created_at, source_type: 'user_chat' }) AS chat_sources
            ORDER BY entityData.name
            """,
            {
                "domain": parameters["domain"],
                "entityNameList": parameters["entityNameList"],
            },
        )
    if pattern == "pattern4":
        label = parameters["entityClass"]
        return (
            f"""
            MATCH (e:{label})
            WHERE (e.domain = $domain OR e.domain STARTS WITH ($domain + '.'))
              AND toLower(e.name) CONTAINS toLower($entityName)
            OPTIONAL MATCH (e)-[r]-(t)
            WHERE (t.domain = $domain OR t.domain STARTS WITH ($domain + '.'))
            WITH e, collect({{
              rel_type: type(r),
              rel_properties: properties(r),
              connected_to: {{label: labels(t), data: properties(t)}}
            }}) AS connections
            OPTIONAL MATCH (chunk:DocChunk)-[:MENTIONS]->(e)
            OPTIONAL MATCH (chat:UserChat)-[:MENTIONS]->(e)
            RETURN properties(e) AS entityData, connections,
                   collect(DISTINCT chunk {{ .id, .name, .doc_id, source_type: 'document' }}) AS doc_sources,
                   collect(DISTINCT chat {{ .id, .session_id, .created_by, .created_at, source_type: 'user_chat' }}) AS chat_sources
            ORDER BY entityData.name
            """,
            {"domain": parameters["domain"], "entityName": parameters["entityName"]},
        )
    if pattern == "pattern5":
        label1 = parameters["entityClass1"]
        label2 = parameters["entityClass2"]
        if parameters["mode"] == "all":
            return (
                f"""
                MATCH (e:{label1}), (t:{label2})
                WHERE (e.domain = $domain OR e.domain STARTS WITH ($domain + '.'))
                  AND (t.domain = $domain OR t.domain STARTS WITH ($domain + '.'))
                  AND toLower(e.name) CONTAINS toLower($entityName1)
                  AND toLower(t.name) CONTAINS toLower($entityName2)
                WITH e, t
                MATCH p = (e)-[*1..5]-(t)
                WITH p
                ORDER BY length(p) ASC
                LIMIT 3
                RETURN [i IN range(0, length(p)-1) | [
                  {{label: labels(nodes(p)[i]), data: properties(nodes(p)[i])}},
                  {{type: type(relationships(p)[i]), data: properties(relationships(p)[i])}}
                ]] + [{{label: labels(nodes(p)[-1]), data: properties(nodes(p)[-1])}}] AS interleavedPath
                """,
                {
                    "domain": parameters["domain"],
                    "entityName1": parameters["entityName1"],
                    "entityName2": parameters["entityName2"],
                },
            )
        return (
            f"""
            MATCH p = shortestPath((e:{label1})-[*..5]-(t:{label2}))
            WHERE (e.domain = $domain OR e.domain STARTS WITH ($domain + '.'))
              AND (t.domain = $domain OR t.domain STARTS WITH ($domain + '.'))
              AND toLower(e.name) CONTAINS toLower($entityName1)
              AND toLower(t.name) CONTAINS toLower($entityName2)
            RETURN [i IN range(0, length(p)-1) | [
              {{labels: labels(nodes(p)[i]), data: properties(nodes(p)[i])}},
              {{rel: type(relationships(p)[i]), data: properties(relationships(p)[i])}}
            ]] + [{{label: labels(nodes(p)[-1]), data: properties(nodes(p)[-1])}}] AS interleavedPath
            """,
            {
                "domain": parameters["domain"],
                "entityName1": parameters["entityName1"],
                "entityName2": parameters["entityName2"],
            },
        )
    if pattern == "pattern6":
        return (
            """
            MATCH (e1)-[r]-(e2)
            WHERE (e1.domain = $domain OR e1.domain STARTS WITH ($domain + '.'))
              AND (e2.domain = $domain OR e2.domain STARTS WITH ($domain + '.'))
              AND toLower(e1.name) CONTAINS toLower($entityName1)
              AND toLower(e2.name) CONTAINS toLower($entityName2)
            RETURN type(r) AS relType,
                   properties(r) AS relProps,
                   startNode(r).name AS fromEntity,
                   endNode(r).name AS toEntity,
                   labels(startNode(r)) AS fromLabels,
                   labels(endNode(r)) AS toLabels
            ORDER BY relType, fromEntity, toEntity
            """,
            {
                "domain": parameters["domain"],
                "entityName1": parameters["entityName1"],
                "entityName2": parameters["entityName2"],
            },
        )
    if pattern == "pattern7":
        return (
            """
            MATCH (e)
            WHERE (e.domain = $domain OR e.domain STARTS WITH ($domain + '.'))
              AND (
                toLower(e.name) CONTAINS toLower($searchTerm)
                OR toLower(coalesce(e.description, '')) CONTAINS toLower($searchTerm)
              )
            RETURN e {.*, label: labels(e)} AS entityData
            ORDER BY e.name
            LIMIT $limit
            """,
            {
                "domain": parameters["domain"],
                "searchTerm": parameters["searchTerm"],
                "limit": parameters.get("limit", 10),
            },
        )
    if pattern == "pattern8":
        label = parameters["entityClass"]
        return (
            f"""
            MATCH (e:{label})-[r]-(t)
            WHERE (e.domain = $domain OR e.domain STARTS WITH ($domain + '.'))
              AND (t.domain = $domain OR t.domain STARTS WITH ($domain + '.'))
              AND toLower(t.name) CONTAINS toLower($entityName)
            RETURN e {{.*, label: labels(e)}} AS entityData,
                   type(r) AS relType,
                   properties(r) AS relProps
            ORDER BY e.name, relType
            """,
            {"domain": parameters["domain"], "entityName": parameters["entityName"]},
        )
    if pattern == "pattern9":
        label = parameters["entityClass"]
        return (
            f"""
            MATCH (e:{label})
            WHERE (e.domain = $domain OR e.domain STARTS WITH ($domain + '.'))
            OPTIONAL MATCH (e)-[r]-()
            WITH e, count(r) AS degree
            RETURN e {{.*, label: labels(e), degree: degree}} AS entityData
            ORDER BY degree DESC, e.name
            LIMIT $topN
            """,
            {"domain": parameters["domain"], "topN": parameters.get("topN", 5)},
        )
    raise ValueError(f"Unsupported graph query pattern: {pattern}")


def execute_pattern(
    domain: str,
    pattern: str,
    question: str | None = None,
    **parameters,
) -> dict:
    params = normalize_pattern_parameters(pattern, {"domain": domain, **parameters})
    validate_pattern_parameters(pattern, params)
    cypher, cypher_params = _pattern_query(pattern, params)
    output_parameters = {
        key: value
        for key, value in params.items()
        if key != "domain" and value is not None
    }
    return {
        "domain": domain,
        "query_type": "graph",
        "command": "pattern",
        "pattern": pattern,
        "question": question,
        "parameters": output_parameters,
        "rows": strip_embeddings(_run_read_query(cypher, cypher_params)),
    }
