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

    Every label carries the same property name, `_domain` — `_`-prefixed
    (Phase 4) as artmind-computed, alongside `_id`; the extraction-contract
    fields the query layer reads directly (name/description/entity_class/
    type/context/aliases) stay unprefixed. This used to differ by label
    (`:Entity._domain` vs. everything else's plain `domain`), which is
    exactly the split docs/CAPABILITIES.md's "Hierarchical domain rollup"
    warned about: "a query that uses the wrong one for the label it matched
    silently scopes nothing" — and which bit twice in practice (see
    `expand_domain_family`'s docstring, and the archived Cypher pattern
    library in docs/DOC_INVENTORY.md). Unified so there is no second name
    left to get wrong.
    """
    return (
        f"({var}._domain IN ${param} "
        f"OR any(dom IN ${param} WHERE {var}._domain STARTS WITH (dom + '.')))"
    )


def expand_domain_family(domain: str) -> list[str]:
    """A domain plus every descendant domain that actually holds data.

    Retrieval paths get hierarchy free via `domain_predicate`'s STARTS WITH
    rollup, but two write/analysis paths need a *concrete list* rather than a
    predicate: normalize_time loads a schema per domain, and candidate_pairs
    restricts ANN neighbours to specific other domains. Both matched `domain`
    exactly before this, so a parent-scoped run silently did nothing.

    Children are derived from the graph rather than the schema directory: no
    filesystem dependency, no cli import, and the result is exactly the domains
    holding data.

    Restricted to :Document and :Entity — both carry a `_domain` index, so the
    STARTS WITH stays index-backed. An unlabelled MATCH (n) would scan every
    node in the database, including the history zone.
    """
    with read_session() as session:
        rows = session.run(
            """
            CALL () {
              MATCH (d:Document) WHERE d._domain STARTS WITH ($d + '.')
              RETURN DISTINCT d._domain AS dom
            UNION
              MATCH (e:Entity) WHERE e._domain STARTS WITH ($d + '.')
              RETURN DISTINCT e._domain AS dom
            }
            RETURN dom
            """,
            d=domain,
        ).data()
    return [domain] + sorted({r["dom"] for r in rows if r.get("dom")})


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


# Node/relationship properties that are internal machinery, never surfaced to a
# query result: the embedding vector and the A1e property-provenance ledger
# (``_prop_sources``). All read paths funnel through strip_internal_props, so
# stripping here means no per-query projection ever leaks them.
_INTERNAL_PROP_KEYS = frozenset({"embedding", "_prop_sources"})


def strip_internal_props(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: strip_internal_props(val)
            for key, val in value.items()
            if key.lower() not in _INTERNAL_PROP_KEYS
        }
    if isinstance(value, list):
        return [strip_internal_props(item) for item in value]
    return value


def serialize_value(value: Any) -> Any:
    if isinstance(value, Node):
        return strip_internal_props(
            {
                "id": value.element_id,
                "labels": list(value.labels),
                "properties": dict(value),
            }
        )
    if isinstance(value, Relationship):
        return strip_internal_props(
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
        return strip_internal_props({str(k): serialize_value(v) for k, v in value.items()})
    if isinstance(value, (list, tuple)):
        return [serialize_value(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def serialize_record(record: Any) -> dict:
    if hasattr(record, "data"):
        return strip_internal_props(serialize_value(record.data()))
    if isinstance(record, dict):
        return strip_internal_props(serialize_value(record))
    return strip_internal_props(serialize_value(dict(record)))


def _run_read_query(cypher: str, parameters: dict) -> list[dict]:
    with read_session() as session:
        return [serialize_record(record) for record in session.run(cypher, **parameters)]


def _domain_output(domains: list[str]) -> dict:
    """Back-compat output keys: always 'domains'; add 'domain' when exactly one."""
    out: dict = {"domains": domains}
    if len(domains) == 1:
        out["domain"] = domains[0]
    return out


def graph_metadata(domains: "str | Sequence[str]") -> dict:
    """The full schema: every node label's properties, every relationship
    type's properties and label-pair connections, scoped to `domains`.

    No `--asOf` (Phase 4) — the projection is current by construction, and
    schema shape doesn't have a valid-time axis to filter on in the first
    place; the option was really about node *currency*, which `--asOf` never
    controlled here anyway (it filtered which nodes counted, not which schema
    entries appeared).
    """
    domains = normalize_domains(domains)
    cypher = f"""
    CALL () {{
      MATCH (n)
      WHERE {domain_predicate("n")}
      UNWIND labels(n) AS label
      WITH label, keys(n) AS nodeKeys, n.type AS typeVal
      UNWIND [k IN nodeKeys WHERE k <> '_prop_sources'] AS propName
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
        "rows": _run_read_query(cypher, {"domains": domains}),
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
      MATCH (o:Observation)
      WHERE {domain_predicate("o")}
      WITH count(o) AS cnt
      RETURN 'Observation' AS label, cnt AS count, null AS names, null AS relationship, null AS from_label, null AS to_label
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
      MATCH (o:Observation)-[r:EXTRACTED_FROM]->(c:DocChunk)
      WHERE {domain_predicate("o")}
      WITH count(r) AS cnt
      RETURN null AS label, cnt AS count, null AS names, 'EXTRACTED_FROM' AS relationship, 'Observation' AS from_label, 'DocChunk' AS to_label
    UNION
      MATCH (e:Entity)-[r:AGGREGATES]->(o:Observation)
      WHERE {domain_predicate("e")}
      WITH count(r) AS cnt
      RETURN null AS label, cnt AS count, null AS names, 'AGGREGATES' AS relationship, 'Entity' AS from_label, 'Observation' AS to_label
    UNION
      MATCH (s:Entity)-[r:RELATES_TO]->(t:Entity)
      WHERE {domain_predicate("s")}
      WITH count(r) AS cnt
      RETURN null AS label, cnt AS count, null AS names, 'RELATES_TO' AS relationship, 'Entity' AS from_label, 'Entity' AS to_label
    }}
    RETURN label, count, names, relationship, from_label, to_label
    """
    return {
        **_domain_output(domains),
        "query_type": "graph",
        "command": "structural_metadata",
        "rows": _run_read_query(cypher, {"domains": domains}),
    }


def filing_vocabulary(
    domains: "str | Sequence[str]" = None,
    min_count: int = 1,
) -> dict:
    """Return the controlled filing vocabulary in the graph (ADR 0012 A5).

    Reads Documents (authoritative for filing metadata per ADR 0010) and returns
    the distinct `project`, `area`, and `tags` values along with their document
    counts. Also returns the distinct `domain` values for completeness. Used by
    the placement classifier (A6) to ground proposals in labels already in use,
    so a suggestion for a new document lands on `Alpha` rather than a fresh
    `proj-alpha` when the vocabulary already carries `Alpha`.

    - ``domains`` optionally scopes vocabulary to specific domain(s).
    - ``min_count`` filters out rare labels (default 1 keeps everything).
    """
    filters = []
    params: dict = {"min_count": max(1, int(min_count))}

    if domains:
        params["domains"] = normalize_domains(domains)
        filters.append(domain_predicate("d"))

    where_clause = " AND ".join(filters) if filters else "true"

    cypher = f"""
    CALL () {{
      MATCH (d:Document)
      WHERE {where_clause} AND d.project IS NOT NULL
      WITH d.project AS value, count(d) AS cnt
      WHERE cnt >= $min_count
      RETURN 'project' AS facet, value, cnt
    UNION
      MATCH (d:Document)
      WHERE {where_clause} AND d.area IS NOT NULL
      WITH d.area AS value, count(d) AS cnt
      WHERE cnt >= $min_count
      RETURN 'area' AS facet, value, cnt
    UNION
      MATCH (d:Document)
      WHERE {where_clause} AND d.tags IS NOT NULL
      UNWIND d.tags AS tag
      WITH tag AS value, count(d) AS cnt
      WHERE cnt >= $min_count
      RETURN 'tags' AS facet, value, cnt
    UNION
      MATCH (d:Document)
      WHERE {where_clause} AND d._domain IS NOT NULL
      WITH d._domain AS value, count(d) AS cnt
      WHERE cnt >= $min_count
      RETURN 'domain' AS facet, value, cnt
    }}
    RETURN facet, value, cnt
    ORDER BY facet, cnt DESC, value
    """
    rows = _run_read_query(cypher, params)

    # Reshape rows into the {project: [...], area: [...], tags: [...], domain: [...]}
    # form the placement classifier and canvas consume — flat rows are noisy for a UI.
    vocab: dict[str, list[dict]] = {"project": [], "area": [], "tags": [], "domain": []}
    for row in rows:
        facet = row.get("facet")
        if facet in vocab:
            vocab[facet].append({"value": row["value"], "count": row["cnt"]})

    return {
        "query_type": "graph",
        "command": "filing_vocabulary",
        "scope": {
            "domains": params.get("domains"),
            "min_count": params["min_count"],
        },
        "vocabulary": vocab,
    }


def filing_listing(
    domains: "str | Sequence[str]" = None,
    project: str | None = None,
    area: str | None = None,
    tags: "str | Sequence[str] | None" = None,
    as_of: str | None = None,
) -> dict:
    """List Documents filtered by filing taxonomy (ADR 0010).

    Any of `domains`, `project`, `area`, `tags` may be omitted; provided filters
    are AND-ed together. `tags` matches if any listed tag is present on the doc.
    Returns rows with document id, name, title, project, area, tags, domain, and
    version — enough for the canvas graph-view Card to render a filtered listing.
    """
    filters = []
    params: dict = {}

    if domains:
        params["domains"] = normalize_domains(domains)
        filters.append(domain_predicate("d"))

    if project:
        params["project"] = project
        filters.append("d.project = $project")

    if area:
        params["area"] = area
        filters.append("d.area = $area")

    if tags:
        tag_list = [tags] if isinstance(tags, str) else list(tags)
        params["tags"] = tag_list
        filters.append("any(t IN $tags WHERE t IN coalesce(d.tags, []))")

    as_of_resolved = resolve_as_of(as_of)
    if as_of_resolved:
        params["asOf"] = as_of_resolved
        filters.append(asof_predicate("d"))

    where_clause = " AND ".join(filters) if filters else "true"

    cypher = f"""
    MATCH (d:Document)
    WHERE {where_clause}
    RETURN d.id AS id,
           d.name AS name,
           d.title AS title,
           d.project AS project,
           d.area AS area,
           d.tags AS tags,
           d._domain AS domain,
           d.version AS version,
           d.created_on AS created_on,
           d.modified_on AS modified_on
    ORDER BY coalesce(d.modified_on, d.created_on, d.name) DESC
    """
    return {
        "query_type": "graph",
        "command": "filing_listing",
        "filters": {
            "domains": params.get("domains"),
            "project": project,
            "area": area,
            "tags": tag_list if tags else None,
        },
        "rows": _run_read_query(cypher, params),
    }


def entity_listing(
    domains: "str | Sequence[str]",
    name_filter: str | None = None,
    count_all: bool = False,
) -> dict:
    """No `--asOf` (Phase 4) — the projection is current by construction, and
    there is nothing left in force "by a date" to filter to: an Entity is
    either asserted right now (it exists) or it isn't (the rebuild deleted
    it)."""
    domains = normalize_domains(domains)
    cypher = f"""
    MATCH (n:Entity)
    WHERE {domain_predicate("n")} AND n.name IS NOT NULL
      AND ($nameFilter IS NULL OR toLower(n.name) CONTAINS toLower($nameFilter))
    UNWIND [l IN labels(n) WHERE l <> 'Entity'] AS label
    WITH label, n.type AS type, collect(DISTINCT n.name) AS names
    RETURN label, collect({{type: type, names: names}}) AS typeGroups
    ORDER BY label
    """
    result: dict = {
        **_domain_output(domains),
        "query_type": "graph",
        "command": "entity_listing",
        "rows": _run_read_query(cypher, {"domains": domains, "nameFilter": name_filter}),
    }
    if name_filter is not None:
        result["name_filter"] = name_filter
    if count_all:
        count_cypher = f"""
        MATCH (n:Entity)
        WHERE {domain_predicate("n")} AND n.name IS NOT NULL
        RETURN count(DISTINCT n) AS total
        """
        count_rows = _run_read_query(count_cypher, {"domains": domains})
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
        return f"{var}._id = ${id_key}"
    cypher_params[name_key] = parameters[name_key]
    return f"toLower({var}.name) CONTAINS toLower(${name_key})"


def _entity_list_selector(parameters: dict, cypher_params: dict, var: str) -> str:
    """WHERE fragment selecting entities by exact ids (preferred) or fuzzy names."""
    if parameters.get("entityIdList"):
        cypher_params["entityIdList"] = parameters["entityIdList"]
        return f"{var}._id IN $entityIdList"
    cypher_params["entityNameList"] = parameters["entityNameList"]
    return f"ANY(n IN $entityNameList WHERE toLower({var}.name) CONTAINS toLower(n))"


def _pattern_query(pattern: str, parameters: dict) -> tuple[str, dict]:
    """Patterns 1-9 no longer accept `--asOf` (Phase 4) — the projection is
    current by construction, so there is nothing "in force by a date" left to
    filter an Entity match to. Only pattern10 keeps it; see that branch for
    what it now means with the History-label split in place.
    """
    if pattern == "pattern1":
        label = parameters["entityClass"]
        return (
            f"""
            MATCH (e:{label})
            WHERE {domain_predicate("e")}
            RETURN e {{.*, label: labels(e)}} AS entityData
            ORDER BY e.name
            LIMIT $limit
            """,
            {"domains": parameters["domains"], "limit": parameters.get("limit", 200)},
        )
    if pattern == "pattern2":
        cypher_params = {"domains": parameters["domains"]}
        selector = _entity_list_selector(parameters, cypher_params, "e")
        return (
            f"""
            MATCH (e:Entity)
            WHERE {domain_predicate("e")}
              AND {selector}
            OPTIONAL MATCH (e)-[:AGGREGATES]->(:Observation)-[:EXTRACTED_FROM]->(chunk:DocChunk)
            WITH e, collect(DISTINCT chunk {{ .id, .name, .doc_id, ._domain, source_type: 'document' }}) AS doc_sources
            RETURN e {{.*, label: labels(e)}} AS entityData,
                   doc_sources
            ORDER BY entityData.name
            """,
            cypher_params,
        )
    if pattern == "pattern3":
        cypher_params = {"domains": parameters["domains"]}
        selector = _entity_list_selector(parameters, cypher_params, "e")
        return (
            f"""
            MATCH (e:Entity)
            WHERE {domain_predicate("e")}
              AND {selector}
            OPTIONAL MATCH (e)-[r:RELATES_TO]-(t:Entity)
            WHERE {domain_predicate("t")}
            WITH e, collect(CASE WHEN r IS NULL THEN NULL ELSE {{
              rel_type: r.rel_type,
              properties: properties(r),
              target: {{name: t.name, label: labels(t)}}
            }} END) AS connections
            OPTIONAL MATCH (e)-[:AGGREGATES]->(:Observation)-[:EXTRACTED_FROM]->(chunk:DocChunk)
            WITH e, connections, collect(DISTINCT chunk {{ .id, .name, .doc_id, ._domain, source_type: 'document' }}) AS doc_sources
            RETURN properties(e) AS entityData, connections, doc_sources
            ORDER BY entityData.name
            """,
            cypher_params,
        )
    if pattern == "pattern4":
        label = parameters["entityClass"]
        cypher_params = {"domains": parameters["domains"]}
        selector = _entity_selector(parameters, cypher_params, "e")
        return (
            f"""
            MATCH (e:{label})
            WHERE {domain_predicate("e")}
              AND {selector}
            OPTIONAL MATCH (e)-[r:RELATES_TO]-(t:Entity)
            WHERE {domain_predicate("t")}
            WITH e, collect(CASE WHEN r IS NULL THEN NULL ELSE {{
              rel_type: r.rel_type,
              rel_properties: properties(r),
              connected_to: {{label: labels(t), data: properties(t)}}
            }} END) AS connections
            OPTIONAL MATCH (e)-[:AGGREGATES]->(:Observation)-[:EXTRACTED_FROM]->(chunk:DocChunk)
            WITH e, connections, collect(DISTINCT chunk {{ .id, .name, .doc_id, ._domain, source_type: 'document' }}) AS doc_sources
            RETURN properties(e) AS entityData, connections, doc_sources
            ORDER BY entityData.name
            """,
            cypher_params,
        )
    if pattern == "pattern5":
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
                ORDER BY e._id, t._id
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
        return (
            f"""
            MATCH (e1:Entity)-[r:RELATES_TO]-(e2:Entity)
            WHERE {domain_predicate("e1")}
              AND {domain_predicate("e2")}
              AND {selector1}
              AND {selector2}
            RETURN r.rel_type AS relType,
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
            WHERE {domain_predicate("e")}
            RETURN e {{.*, label: labels(e)}} AS entityData
            ORDER BY ftScore DESC, e.name
            LIMIT $limit
            """,
            {
                "domains": parameters["domains"],
                "searchTerm": search_term,
                "limit": parameters.get("limit", 10),
            },
        )
    if pattern == "pattern8":
        label = parameters["entityClass"]
        cypher_params = {"domains": parameters["domains"]}
        selector = _entity_selector(parameters, cypher_params, "t")
        return (
            f"""
            MATCH (e:{label})-[r:RELATES_TO]-(t:Entity)
            WHERE {domain_predicate("e")}
              AND {domain_predicate("t")}
              AND {selector}
            RETURN e {{.*, label: labels(e)}} AS entityData,
                   r.rel_type AS relType,
                   properties(r) AS relProps
            ORDER BY e.name, relType
            """,
            cypher_params,
        )
    if pattern == "pattern9":
        label = parameters["entityClass"]
        degree_mode = parameters.get("degreeMode", "relations")
        if degree_mode == "mentions":
            # mentions: how often sources mention the entity — via the
            # projection's own AGGREGATES edge to the observations that fed
            # it, one hop further to their source chunks.
            degree_body = """
            OPTIONAL MATCH (e)-[r1:AGGREGATES]->(:Observation)-[:EXTRACTED_FROM]->(:DocChunk)
            WITH e, count(DISTINCT r1) AS degree
            """
        else:
            # relations: entity-entity connectivity (neighbor domain-scoped like
            # every other entity match); all: every edge including structural ones.
            degree_match = {
                "relations": f'OPTIONAL MATCH (e)-[r:RELATES_TO]-(t:Entity) WHERE {domain_predicate("t")}',
                "all": "OPTIONAL MATCH (e)-[r]-()",
            }[degree_mode]
            degree_body = f"""
            {degree_match}
            WITH e, count(r) AS degree
            """
        return (
            f"""
            MATCH (e:{label})
            WHERE {domain_predicate("e")}
            {degree_body}
            RETURN e {{.*, label: labels(e), degree: degree}} AS entityData
            ORDER BY degree DESC, e.name
            LIMIT $topN
            """,
            {"domains": parameters["domains"], "topN": parameters.get("topN", 5)},
        )
    if pattern == "pattern10":
        # `--asOf` (kept, Phase 4) no longer means "ignored". Chunks carry no
        # valid-time of their own (never stamped at ingest — a chunk's
        # currency is entirely a function of its document's), so there is no
        # date to compare against; what the label swap DOES give this pattern
        # for the first time is a real history pool to reach into. Without
        # --asOf: only the document's CURRENT self (:Document) and CURRENT
        # chunks (:DocChunk), as always. With --asOf (any value — a presence
        # flag here, not a point in time): also reach :DocumentHistory /
        # :DocChunkHistory, so a retired document and its retired/superseded
        # chunks become visible too. Coarser than "in force by T" elsewhere,
        # and said so in the CLI help rather than implied.
        as_of = parameters.get("asOf")
        doc_return = "d { .id, .name, .path, ._domain, .valid_from, .valid_to, .superseded_by } AS document"
        chunk_return = "c { .id, .name, .doc_id, ._domain, .valid_to, .text } AS chunk"
        if as_of:
            cypher = f"""
            CALL () {{
              MATCH (d:Document)
              WHERE {domain_predicate("d")} AND toLower(d.name) CONTAINS toLower($documentName)
              RETURN d
            UNION
              MATCH (d:DocumentHistory)
              WHERE {domain_predicate("d")} AND toLower(d.name) CONTAINS toLower($documentName)
              RETURN d
            }}
            WITH d
            MATCH (c)-[:PART_OF]->(d)
            WHERE c:DocChunk OR c:DocChunkHistory
            RETURN {doc_return}, {chunk_return}
            ORDER BY c.id
            """
        else:
            cypher = f"""
            MATCH (d:Document)
            WHERE {domain_predicate("d")} AND toLower(d.name) CONTAINS toLower($documentName)
            MATCH (c:DocChunk)-[:PART_OF]->(d)
            RETURN {doc_return}, {chunk_return}
            ORDER BY c.id
            """
        return (
            cypher,
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
        "rows": strip_internal_props(_run_read_query(cypher, cypher_params)),
    }
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
        neighbor_call = f"""
    CALL (c) {{
      MATCH (s:DocChunk {{doc_id: c.doc_id}})
      WITH c, s
      ORDER BY s.id
      WITH c, collect(s) AS sibs
      WITH sibs, coalesce([i IN range(0, size(sibs)-1) WHERE sibs[i].id = c.id][0], -1) AS idx
      RETURN [j IN range(idx - $expand, idx + $expand)
              WHERE idx >= 0 AND j >= 0 AND j < size(sibs) AND j <> idx
              | {{id: sibs[j].id, name: sibs[j].name, valid_to: sibs[j].valid_to, text: sibs[j].text}}] AS neighbors
    }}"""
        neighbor_return = ",\n           neighbors"
    return f"""
    MATCH (c:DocChunk)
    WHERE c.id IN $chunkIds
      AND {domain_predicate("c")}{asof_c}
    OPTIONAL MATCH (c)-[:PART_OF]->(d:Document){neighbor_call}
    RETURN c {{ .id, .name, .doc_id, ._domain, .valid_from, .valid_to, .text }} AS chunk,
           d {{ .id, .name, .path, ._domain, .valid_from, .valid_to, .superseded_by }} AS document{neighbor_return}
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


def _entity_context_query() -> str:
    """Cypher for entity_context: entity + one-hop relationships + source text.

    No `--asOf` (Phase 4) — the entity and its projected chunks are current by
    construction. Chunks are ordered current-first (valid_to IS NULL), then by
    id; the first $includeChunks are returned with text, the rest as ids only.

    Source chunks are reached via `(e)-[:AGGREGATES]->(:Observation)
    -[:EXTRACTED_FROM]->(c:DocChunk)` — an Entity has never had a direct
    EXTRACTED_FROM edge since Phase 3 moved that provenance onto observations;
    matching `(e)-[:EXTRACTED_FROM]->(c)` directly (as this query did before)
    silently returned zero chunks for every entity.
    """
    return f"""
    MATCH (e:Entity {{_id: $entityId}})
    WHERE {domain_predicate("e")}
    OPTIONAL MATCH (e)-[r:RELATES_TO]-(t:Entity)
    WHERE {domain_predicate("t")}
    WITH e, collect(CASE WHEN r IS NULL THEN NULL ELSE {{
      rel_type: r.rel_type,
      properties: properties(r),
      target: {{id: t._id, name: t.name, label: labels(t)}}
    }} END) AS connections
    OPTIONAL MATCH (e)-[:AGGREGATES]->(:Observation)-[:EXTRACTED_FROM]->(c:DocChunk)
    OPTIONAL MATCH (c)-[:PART_OF]->(d:Document)
    WITH e, connections, c, d
    ORDER BY c.valid_to IS NULL DESC, c.id
    WITH e, connections, [x IN collect(CASE WHEN c IS NULL THEN NULL ELSE c {{
      .id, .name, .doc_id, ._domain, .valid_to, .text,
      document: d {{ .id, .name, ._domain, .valid_from, .valid_to, .superseded_by }}
    }} END) WHERE x IS NOT NULL] AS allChunks
    RETURN e {{.*, label: labels(e)}} AS entityData,
           [x IN connections WHERE x IS NOT NULL] AS connections,
           allChunks[0..$includeChunks] AS chunks,
           [x IN allChunks[$includeChunks..] | {{id: x.id, name: x.name, doc_id: x.doc_id}}] AS more_chunks
    """


def entity_context(
    domains: "str | Sequence[str]",
    entity_id: str,
    include_chunks: int = 5,
) -> dict:
    """One-call grounded picture of a resolved entity: properties, one-hop
    relationships, and the text of its most current source chunks. Replaces
    the pattern4 + chunk-fetch sequence for entity-anchored questions."""
    domains = normalize_domains(domains)
    if not (entity_id or "").strip():
        raise ValueError("--entityId is required")
    include_chunks = int(include_chunks)
    if include_chunks < 0:
        raise ValueError("--includeChunks must be >= 0")
    cypher = _entity_context_query()
    params = {
        "domains": domains,
        "entityId": entity_id.strip(),
        "includeChunks": include_chunks,
    }
    return {
        **_domain_output(domains),
        "query_type": "graph",
        "command": "entity_context",
        "parameters": {"entityId": entity_id.strip(), "includeChunks": include_chunks},
        "rows": strip_internal_props(_run_read_query(cypher, params)),
    }


def domains_overview() -> dict:
    """One aggregation grouped by n._domain: doc names/counts, entity counts, top classes.

    The cheap routing input that maps an area ("banking") to concrete sibling domains.
    """
    cypher = """
    CALL () {
      MATCH (d:Document)
      RETURN d._domain AS domain, 'documents' AS k,
             count(d) AS c, collect(DISTINCT d.name)[0..25] AS names
    UNION
      MATCH (e:Entity)
      RETURN e._domain AS domain, 'entities' AS k, count(e) AS c, null AS names
    UNION
      MATCH (e:Entity)
      WITH e._domain AS domain, e.entity_class AS cls, count(*) AS n
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
        raw = row["domain"]
        # `domain` is written as a scalar string, but a node carrying a
        # list-valued domain (multi-domain / bad data) must not crash the
        # aggregation on an unhashable dict key. Attribute such a row to each
        # concrete domain instead, and accumulate/union so an expanded element
        # merges cleanly with any scalar row for the same domain. For the normal
        # one-row-per-scalar-domain case this is behaviour-identical.
        keys = (
            [d for d in dict.fromkeys(raw) if d is not None]
            if isinstance(raw, list)
            else [raw]
        )
        for d in keys:
            entry = overview.setdefault(d, {"domain": d})
            for p in row["parts"]:
                if p["k"] == "documents":
                    entry["document_count"] = entry.get("document_count", 0) + p["c"]
                    merged = list(dict.fromkeys((entry.get("documents") or []) + (p["names"] or [])))
                    entry["documents"] = merged[:25]
                elif p["k"] == "entities":
                    entry["entity_count"] = entry.get("entity_count", 0) + p["c"]
                elif p["k"] == "top_classes":
                    merged = list(dict.fromkeys((entry.get("top_classes") or []) + (p["names"] or [])))
                    entry["top_classes"] = merged[:8]

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
    WHERE {domain_predicate("a")} AND {domain_predicate("b")} AND a._id < b._id
    WITH r.conflict_id AS conflictId, r.aspect AS aspect,
         collect(DISTINCT a {{ ._id, .name, .entity_class, ._domain }})
           + collect(DISTINCT b {{ ._id, .name, .entity_class, ._domain }}) AS entities
    WHERE ($entityIds = [] OR any(e IN entities WHERE e._id IN $entityIds))
      AND ($entityName IS NULL OR any(e IN entities WHERE toLower(e.name) CONTAINS toLower($entityName)))
    OPTIONAL MATCH (co:Conflict {{id: conflictId}})
    WITH conflictId, aspect, entities, co, coalesce(co.status, 'open') AS effectiveStatus
    WHERE $status = 'all' OR effectiveStatus = $status
    OPTIONAL MATCH (co)-[ev:EVIDENCE]->(c:DocChunk)
    WITH conflictId, aspect, entities, co, effectiveStatus,
         [x IN collect(CASE WHEN c IS NULL THEN NULL ELSE {{
           side: ev.side, chunk_id: c.id, doc_id: c.doc_id, domain: c._domain, text: c.text
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


def timeline(
    domains: "str | Sequence[str]",
    from_: str | None = None,
    to: str | None = None,
) -> dict:
    """Domain-scoped: every entity of a `kind: occurrent` class, ordered by
    `valid_from`, windowed by `--from`/`--to`.

    Re-specified (Phase 4) from a per-entity two-hop relationship traversal
    that pulled `event_at`/`valid_from` off *neighbouring* entities and their
    connecting edges (deleted, along with `event_at` and its index — for an
    occurrent entity `valid_from` already IS the event date, so a second axis
    was redundant). "The timeline" is now a property of a domain's occurrent
    classes, not of one entity's neighbours, which is also why `--entityId` is
    gone — an entity-scoped history now lives in `entity-history`, a
    fact-level command over one entity's *observations*; this one is a
    class-level preset over the same Entity-matching shape `entity_listing`
    uses (domain-scoped, no `--asOf` — the same reasoning applies: the
    projection is current by construction).
    """
    from artmind.temporal import load_schema

    domains = normalize_domains(domains)
    occurrent_labels: set[str] = set()
    for domain in domains:
        schema = load_schema(domain)
        for cls, decl in (schema.get("entity_types") or {}).items():
            if (decl or {}).get("kind") == "occurrent":
                try:
                    occurrent_labels.add(normalize_entity_class(cls))
                except ValueError:
                    continue

    if not occurrent_labels:
        return {**_domain_output(domains), "query_type": "graph", "command": "timeline", "rows": []}

    params: dict = {"domains": domains, "labels": sorted(occurrent_labels)}
    window = ""
    if from_:
        params["from_"] = from_
        window += "\n      AND (e.valid_from IS NULL OR e.valid_from >= $from_)"
    if to:
        params["to"] = to
        window += "\n      AND (e.valid_from IS NULL OR e.valid_from <= $to)"
    cypher = f"""
    MATCH (e:Entity)
    WHERE {domain_predicate("e")}
      AND any(l IN labels(e) WHERE l IN $labels){window}
    RETURN e {{ ._id, .name, .entity_class, label: labels(e), .valid_from, .valid_to }} AS entity
    ORDER BY e.valid_from
    """
    return {
        **_domain_output(domains),
        "query_type": "graph",
        "command": "timeline",
        "window": {"from": from_, "to": to},
        "rows": _run_read_query(cypher, params),
    }


_PROPERTY_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def entity_history(
    domains: "str | Sequence[str]",
    entity_id: str,
    as_of: str | None = None,
    property: str | None = None,
) -> dict:
    """Every observation behind one entity, ordered by the **fact-level**
    valid-time axis (`_valid_from`/`_valid_to`) — "what was this worth at
    time T", never `_doc_valid_from` (document-level; that only decides the
    projection's *winner* — see projection-pipeline.md, "Two valid-time axes
    on every observation"). Answers a question `entity-context` structurally
    cannot: that command only ever sees `:Observation` (current), by design.

    Spans **both** `:Observation` and `:ObservationHistory` — a document
    being retired doesn't make what it once asserted stop having happened.

    `--property` narrows to one property's value at each point rather than
    the full observation. `--asOf` here means "every fact in force by this
    date" (`_valid_from <= asOf`), the same floor-not-snapshot meaning as
    everywhere else `--asOf` survives (see the option's own help text).

    Resolves `--entityId` through the live `:Entity` node's `key` property —
    an entity with zero remaining observations anywhere (fully retired) has
    no `:Entity` node left to resolve through, and this command has nothing
    to look the id up against in that case. It is for a still-projecting
    entity's full fact history, not a fully-retired one.
    """
    domains = normalize_domains(domains)
    as_of = resolve_as_of(as_of)
    entity_id = (entity_id or "").strip()
    if not entity_id:
        raise ValueError("--entityId is required")
    if property is not None and not _PROPERTY_NAME_RE.match(property):
        raise ValueError(f"--property must be a plain identifier; got {property!r}")

    params: dict = {"domains": domains, "entityId": entity_id}
    filters = []
    if as_of:
        params["asOf"] = as_of
        filters.append("(o._valid_from IS NULL OR o._valid_from <= $asOf)")
    if property:
        params["property"] = property
        filters.append("o[$property] IS NOT NULL")
    filter_clause = "".join(f"\n      AND {f}" for f in filters)

    cypher = f"""
    MATCH (ent:Entity {{_id: $entityId}})
    WHERE {domain_predicate("ent")}
    WITH ent.key AS key
    MATCH (o)
    WHERE (o:Observation OR o:ObservationHistory)
      AND o.key = key{filter_clause}
    RETURN o {{.*}} AS observation
    ORDER BY o._valid_from, o.doc_id
    """
    rows = strip_internal_props(_run_read_query(cypher, params))
    if property:
        rows = [
            {
                "value": row["observation"].get(property),
                "valid_from": row["observation"].get("_valid_from"),
                "valid_to": row["observation"].get("_valid_to"),
                "doc_id": row["observation"].get("doc_id"),
                "observation_id": row["observation"].get("id"),
            }
            for row in rows
        ]
    return {
        **_domain_output(domains),
        "query_type": "graph",
        "command": "entity_history",
        "entity_id": entity_id,
        "parameters": {
            **({"asOf": as_of} if as_of else {}),
            **({"property": property} if property else {}),
        },
        "rows": rows,
    }
