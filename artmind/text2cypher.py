import json
import re

from loguru import logger
from neo4j.exceptions import Neo4jError

from artmind.extraction import call_llm, parse_json_response
from artmind.graph_query import (
    _run_read_query,
    entity_listing,
    graph_metadata,
    strip_internal_props,
)
from utils.functions import load_env, resolve_llm_model


_WRITE_KEYWORDS_RE = re.compile(
    r"\b(CREATE|DELETE|DETACH|SET|REMOVE|MERGE|DROP|FOREACH)\b"
    r"|\bLOAD\s+CSV\b"
    r"|\bapoc\.(create|merge|refactor|atomic|periodic|trigger|load|export|import"
    r"|cypher\.do|cypher\.runWrite|nodes\.delete)"
    r"|CALL\s*\{[^}]*\}\s*IN\s+TRANSACTIONS",
    re.IGNORECASE,
)


def validate_read_only(cypher: str) -> None:
    """Raise ValueError if the Cypher contains write operations."""
    match = _WRITE_KEYWORDS_RE.search(cypher)
    if match:
        raise ValueError(
            f"Generated Cypher contains a write operation ({match.group()!r}) "
            "and cannot be executed. Only read queries are allowed."
        )


def validate_domain_scoped(cypher: str) -> None:
    """Raise ValueError if the Cypher never references $domains.

    The prompt instructs the model to scope every unbound node with a WHERE
    clause built on $domains, but nothing forces it to comply — unlike the
    templated patterns and vector search, which inject domain_predicate()
    into the query themselves. This is a heuristic backstop against the LLM
    dropping the scoping clause entirely, not a proof the predicate covers
    every matched variable: a query that merely mentions $domains without
    applying it to every node still passes this check.
    """
    if "$domains" not in cypher:
        raise ValueError(
            "Generated Cypher does not reference $domains and cannot be "
            "verified as scoped to the requested domain(s). Refusing to "
            "execute — this would otherwise read across every domain."
        )


def _schema_summary(metadata: dict) -> str:
    """Format graph_metadata() output into a compact text block for the prompt."""
    lines: list[str] = []
    for row in metadata.get("rows", []):
        category = row.get("category", "")
        name = row.get("name", "")
        props = row.get("propertyNames", [])
        if category == "nodes":
            types = row.get("distinctTypes", [])
            type_str = f"  types={types}" if types else ""
            lines.append(f"  Node :{name}  properties={props}{type_str}")
        elif category == "relationships":
            conns = row.get("connections", [])
            conn_strs = []
            for c in (conns or []):
                from_labels = c.get("from", [])
                to_labels = c.get("to", [])
                conn_strs.append(f"(:{','.join(from_labels)})-[:{name}]->(:{','.join(to_labels)})")
            lines.append(f"  Relationship :{name}  properties={props}  {', '.join(conn_strs)}")
    return "\n".join(lines) if lines else "  (no schema information available)"


def _entities_summary(listing: dict) -> str:
    """Format entity_listing() output into a compact text block for the prompt."""
    lines: list[str] = []
    for row in listing.get("rows", []):
        label = row.get("label", "")
        type_groups = row.get("typeGroups", [])
        for tg in type_groups:
            names = tg.get("names", [])
            if names:
                sample = names[:20]
                suffix = f" ... ({len(names)} total)" if len(names) > 20 else ""
                lines.append(f"  :{label}  names={sample}{suffix}")
    return "\n".join(lines) if lines else "  (no entities found)"


# Hardcoded structural schema that is always included in the text2cypher prompt.
# This ensures the LLM knows the exact relationships between Document, DocChunk,
# UserChat, and Entity nodes — preventing guesswork on relationship names.
STRUCTURAL_SCHEMA = """\
STRUCTURAL GRAPH (fixed for all domains — use these exact relationship names):
  Node :Document  properties=[id, name, path, domain]
  Node :DocChunk  properties=[id, name, doc_id, text, domain, embedding]
  Node :UserChat  properties=[id, raw_text, domain, session_id, created_by, created_at, embedding]
  Node :Entity    properties=[id, name, entity_class, domain, description, type]
  Relationship (:DocChunk)-[:PART_OF]->(:Document)        — chunk belongs to a document
  Relationship (:Entity)-[:EXTRACTED_FROM]->(:DocChunk)    — entity was extracted from a chunk
    (use this, reversed, to find which chunks/documents mention an entity — there is
     no separate (:DocChunk)-[:MENTIONS]->(:Entity) edge)
  Relationship (:UserChat)-[:MENTIONS]->(:Entity)          — user chat mentions an entity
  Node :Conflict  properties=[id, aspect, claim_a, claim_b, severity, status, domains, detected_at, detected_by_model]
  Relationship (:Conflict)-[:CONFLICT_OF]->(:Entity)      — both sides of a conflict
  Relationship (:Conflict)-[:EVIDENCE {side}]->(:DocChunk) — competing claim text
  Relationship (:Entity)-[:CONFLICTS_WITH {conflict_id, aspect}]->(:Entity)
  Relationship (:Document)-[:SUPERSEDES {scope, effective}]->(:Document)  — newer replaces older
  Node :EntityVersion properties=[id, entity_id, name, entity_class, domain, valid_from, valid_to, closed_by, superseded_by_doc, snapshot_at]
  Relationship (:Entity)-[:PRIOR_STATE]->(:EntityVersion) — a superseded snapshot of that entity's values
    (history only — never traverse into it for "what is true now" questions; the
     live :Entity node always holds current values)
  Timed nodes carry valid_from/valid_to; superseded docs also carry superseded_by.
  Entity-to-Entity relationships are domain-specific (see GRAPH SCHEMA below)."""


def build_text2cypher_prompt(
    question: str,
    schema_info: str,
    entities_info: str,
    domains: list[str],
) -> str:
    domains_json = json.dumps(domains)
    return f"""\
You are a Neo4j Cypher expert. Given the graph schema and entity listing below,
write a READ-ONLY Cypher query that answers the user's question.

RULES:
- The query MUST be read-only. Never use CREATE, DELETE, DETACH, SET, REMOVE, MERGE, or DROP.
- Always scope results to the domains by including a WHERE clause:
    (n.domain IN $domains OR any(_dom IN $domains WHERE n.domain STARTS WITH (_dom + '.')))
  Apply this filter to every unbound node in the MATCH pattern. Always use `_dom`
  (exactly as shown) as the any() loop variable — never reuse a node's own alias
  (e.g. `d` for a :Document node) there, since that shadows the node inside the
  any() clause and produces a Cypher type-mismatch error at query time.
- Use entity names exactly as they appear in the entity listing when matching.
- For Document/DocChunk/UserChat/Entity queries, use ONLY the relationship names
  from the STRUCTURAL GRAPH section below. Do NOT invent relationship names.
- Extracted entities always carry the :Entity label in addition to their class
  label. Label entity nodes explicitly (e.g. (p:PERSON) or (e:Entity)); never
  match bare unlabeled nodes.
- In variable-length paths between entities, keep the path in entity space:
  add `all(x IN nodes(p) WHERE x:Entity)` to the WHERE clause. Otherwise paths
  degenerate to co-mention hops through DocChunk nodes.
- Return meaningful column aliases.
- Keep the query concise.
- If the question is present-tense ("who CAN approve…"), add a validity filter to
  timed nodes: ($asOf IS NULL OR ((n.valid_from IS NULL OR n.valid_from <= $asOf)
  AND (n.valid_to IS NULL OR n.valid_to > $asOf))); include "asOf" in parameters.
  For historical questions ("what WAS the limit in 2024?") set asOf to that date.
- :EntityVersion nodes and PRIOR_STATE edges are history only — a superseded
  snapshot of an entity's prior values. Never traverse into them to answer a
  "what is true now" question; the live :Entity node always holds current
  values. Only use :EntityVersion when the question explicitly asks about a
  prior/historical state of a specific entity.

{STRUCTURAL_SCHEMA}

GRAPH SCHEMA (domains: {domains}):
{schema_info}

ENTITY LISTING (domains: {domains}):
{entities_info}

USER QUESTION:
{question}

Respond with ONLY a JSON object (no markdown fencing, no explanation) with these keys:
- "cypher": the Cypher query string (use $domains as parameter for the domain values)
- "parameters": a JSON object of query parameters (always include "domains": {domains_json})
"""


def generate_cypher(
    question: str,
    domains,
    model: str | None = None,
) -> dict:
    """Generate a Cypher query from a natural-language question.

    Returns a dict with keys: cypher, parameters.
    """
    from artmind.graph_query import normalize_domains

    domains = normalize_domains(domains)

    env = load_env()
    resolved_model = resolve_llm_model(env, model)

    metadata = graph_metadata(domains)
    listing = entity_listing(domains)

    schema_info = _schema_summary(metadata)
    entities_info = _entities_summary(listing)

    prompt = build_text2cypher_prompt(question, schema_info, entities_info, domains)
    raw = call_llm(resolved_model, prompt)
    parsed = parse_json_response(raw)

    if isinstance(parsed, list):
        parsed = parsed[0] if parsed else {}

    cypher = parsed.get("cypher", "")
    parameters = parsed.get("parameters", {})

    if not cypher:
        raise ValueError("LLM did not return a Cypher query")

    parameters.setdefault("domains", domains)

    validate_read_only(cypher)
    validate_domain_scoped(cypher)

    logger.info("text2cypher generated: {}", cypher)
    return {"cypher": cypher, "parameters": parameters}


def execute_text2cypher(
    question: str,
    domains,
    model: str | None = None,
    dry_run: bool = False,
) -> dict:
    """Generate and optionally execute a Cypher query from natural language.

    If dry_run is True, returns the generated Cypher without executing it.
    """
    from artmind.graph_query import _domain_output, normalize_domains

    domains = normalize_domains(domains)
    result = generate_cypher(question, domains, model)
    cypher = result["cypher"]
    parameters = result["parameters"]

    output: dict = {
        **_domain_output(domains),
        "query_type": "graph",
        "command": "text2cypher",
        "question": question,
        "generated_cypher": cypher,
        "parameters": {k: v for k, v in parameters.items() if k != "domains"},
    }

    if dry_run:
        output["rows"] = []
        output["dry_run"] = True
        return output

    try:
        output["rows"] = strip_internal_props(_run_read_query(cypher, parameters))
    except Neo4jError as exc:
        # neo4j exception classes often carry an empty str()/repr() (message lives on
        # .message/.code instead), so a bare `raise` or `str(exc)` surfaces nothing
        # useful — always report the generated Cypher alongside whatever detail the
        # driver did give us.
        detail = getattr(exc, "message", None) or str(exc) or exc.__class__.__name__
        raise ValueError(
            f"Generated Cypher failed ({getattr(exc, 'code', exc.__class__.__name__)}): {detail}\n"
            f"Cypher: {cypher}\n"
            f"Parameters: {parameters}"
        ) from exc
    return output
