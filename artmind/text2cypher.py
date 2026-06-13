import re

from loguru import logger

from artmind.extraction import call_llm, parse_json_response
from artmind.graph_query import (
    _run_read_query,
    entity_listing,
    graph_metadata,
    strip_embeddings,
)
from utils.functions import load_env


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
  Relationship (:DocChunk)-[:MENTIONS]->(:Entity)          — chunk mentions an entity
  Relationship (:UserChat)-[:MENTIONS]->(:Entity)          — user chat mentions an entity
  Entity-to-Entity relationships are domain-specific (see GRAPH SCHEMA below)."""


def build_text2cypher_prompt(
    question: str,
    schema_info: str,
    entities_info: str,
    domain: str,
) -> str:
    return f"""\
You are a Neo4j Cypher expert. Given the graph schema and entity listing below,
write a READ-ONLY Cypher query that answers the user's question.

RULES:
- The query MUST be read-only. Never use CREATE, DELETE, DETACH, SET, REMOVE, MERGE, or DROP.
- Always scope results to the domain by including a WHERE clause:
    (n.domain = '{domain}' OR n.domain STARTS WITH ('{domain}' + '.'))
  Apply this filter to every unbound node in the MATCH pattern.
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

{STRUCTURAL_SCHEMA}

GRAPH SCHEMA (domain: {domain}):
{schema_info}

ENTITY LISTING (domain: {domain}):
{entities_info}

USER QUESTION:
{question}

Respond with ONLY a JSON object (no markdown fencing, no explanation) with these keys:
- "cypher": the Cypher query string (use $domain as parameter for the domain value)
- "parameters": a JSON object of query parameters (always include "domain": "{domain}")
"""


def generate_cypher(
    question: str,
    domain: str,
    model: str | None = None,
) -> dict:
    """Generate a Cypher query from a natural-language question.

    Returns a dict with keys: cypher, parameters.
    """
    env = load_env()
    resolved_model = model or env.get("ARTMIND_KG_LLM_MODEL", "ministral-3:14b")

    metadata = graph_metadata(domain)
    listing = entity_listing(domain)

    schema_info = _schema_summary(metadata)
    entities_info = _entities_summary(listing)

    prompt = build_text2cypher_prompt(question, schema_info, entities_info, domain)
    raw = call_llm(resolved_model, prompt)
    parsed = parse_json_response(raw)

    if isinstance(parsed, list):
        parsed = parsed[0] if parsed else {}

    cypher = parsed.get("cypher", "")
    parameters = parsed.get("parameters", {})

    if not cypher:
        raise ValueError("LLM did not return a Cypher query")

    parameters.setdefault("domain", domain)

    validate_read_only(cypher)

    logger.info("text2cypher generated: {}", cypher)
    return {"cypher": cypher, "parameters": parameters}


def execute_text2cypher(
    question: str,
    domain: str,
    model: str | None = None,
    dry_run: bool = False,
) -> dict:
    """Generate and optionally execute a Cypher query from natural language.

    If dry_run is True, returns the generated Cypher without executing it.
    """
    result = generate_cypher(question, domain, model)
    cypher = result["cypher"]
    parameters = result["parameters"]

    output: dict = {
        "domain": domain,
        "query_type": "graph",
        "command": "text2cypher",
        "question": question,
        "generated_cypher": cypher,
        "parameters": {k: v for k, v in parameters.items() if k != "domain"},
    }

    if dry_run:
        output["rows"] = []
        output["dry_run"] = True
        return output

    output["rows"] = strip_embeddings(_run_read_query(cypher, parameters))
    return output
