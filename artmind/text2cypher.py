import json
import math
import re
from urllib.parse import quote, urlsplit, urlunsplit

from loguru import logger
from neo4j.exceptions import Neo4jError

from artmind.extraction import call_llm, parse_json_response
from artmind.graph_query import (
    _run_read_query,
    entity_listing,
    graph_metadata,
    strip_internal_props,
)
from artmind.structural_schema import render_prompt_block
from utils.functions import load_env, resolve_llm_model


# The LLM is told to "keep the query concise" but nothing enforces a LIMIT --
# an unscoped MATCH over a dense domain can return as many rows as pattern3/4
# ever could. Capped the same way as every other unbounded output here: the
# full result is still fetched (there's no generic way to push a LIMIT into
# an arbitrary LLM-generated query without risking a semantically wrong
# answer), but only the first `TEXT2CYPHER_MAX_ROWS` are returned, with
# `rows_total`/`truncated` alongside.
TEXT2CYPHER_MAX_ROWS = 200


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


def _empty_domain_warnings(listing: dict, domains: list[str]) -> list[str]:
    """One warning when the entity listing for `domains` holds no entity at all.

    The query is then written blind (the model has no names or types to ground
    on) and will match nothing; the usual cause is the wrong domain.
    """
    for row in listing.get("rows", []):
        if any(tg.get("names") for tg in row.get("typeGroups", [])):
            return []
    return [
        f"No entities found in domain(s) {', '.join(domains)}: the generated Cypher "
        "was written without any entity names or types and will probably return "
        "nothing. Check the domain."
    ]


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


# Structural schema block always included in the text2cypher prompt, so the LLM
# knows the exact relationships between Document, DocChunk, UserChat, Observation,
# Entity, and Conflict nodes — preventing guesswork on relationship names. Rendered
# from artmind/structural_schema.py, the one place these facts are declared as data;
# see that module's docstring for why (it's also what the artmind-query skill's own
# "Fixed Structural Schema" section is checked against, in
# test/test_query_skill_structural_schema.py).
STRUCTURAL_SCHEMA = render_prompt_block()


# Appended to the prompt when the result is to be DRAWN (Neo4j Browser) rather
# than read as a table: the visualisation only shows what comes back as
# nodes / relationships / paths, so scalar columns would render as nothing.
GRAPH_MODE_RULES = """\
- GRAPH MODE: the result will be drawn as a graph, not read as a table. RETURN whole
  nodes and relationships (e.g. `RETURN a, r, b`) or whole paths (`RETURN p`) -- never
  only scalar properties such as `a.name`. Bind every relationship you want drawn to
  a variable and return it. Do not use CALL.
- GRAPH MODE: keep the drawing legible, but bound ENTITIES, not paths. When one entity can
  have many paths (an entity with many observations), pick the entities first
  (`WITH e, collect(p) AS paths ORDER BY ... LIMIT 10`), then `UNWIND paths AS p RETURN p`
  and end with a generous `LIMIT 500`. A final `LIMIT 100` on raw paths lets the
  busiest entity fill every row and hide the others.
"""


def build_text2cypher_prompt(
    question: str,
    schema_info: str,
    entities_info: str,
    domains: list[str],
    graph_mode: bool = False,
) -> str:
    domains_json = json.dumps(domains)
    graph_rules = GRAPH_MODE_RULES if graph_mode else ""
    return f"""\
You are a Neo4j Cypher expert. Given the graph schema and entity listing below,
write a READ-ONLY Cypher query that answers the user's question.

RULES:
- The query MUST be read-only. Never use CREATE, DELETE, DETACH, SET, REMOVE, MERGE, or DROP.
- Always scope results to the domains by including a WHERE clause. EVERY label carries
  the SAME domain property name, `_domain` (leading underscore, no exceptions):
    (n._domain IN $domains OR any(_dom IN $domains WHERE n._domain STARTS WITH (_dom + '.')))
  Apply this filter to every unbound node in the MATCH pattern, whatever its label. Always
  use `_dom` (exactly as shown) as the any() loop variable — never reuse a node's own alias
  (e.g. `d` for a :Document node) there, since that shadows the node inside the any()
  clause and produces a Cypher type-mismatch error.
- Use entity names exactly as they appear in the entity listing when matching.
- For Document/DocChunk/UserChat/Observation/Entity queries, use ONLY the relationship
  names from the STRUCTURAL GRAPH section below. Do NOT invent relationship names, and
  do NOT invent a relationship TYPE for an entity-to-entity edge — there is only one,
  RELATES_TO, and the real-world meaning is its `rel_type` property (filter with
  `r.rel_type = '...'`, never a made-up `-[:VERB]->`).
- Extracted entities always carry the :Entity label in addition to their class
  label. Label entity nodes explicitly (e.g. (p:PERSON) or (e:Entity)); never
  match bare unlabeled nodes. Entity's id property is `_id`, not `id`.
- In variable-length paths between entities, keep the path in entity space:
  add `all(x IN nodes(p) WHERE x:Entity)` to the WHERE clause. Otherwise paths
  degenerate to co-mention hops through DocChunk nodes.
- On an Entity node, `entity_class` is its class label (e.g. 'LOCATION', 'PERSON') -- a
  fixed vocabulary, never a free-text description. The finer kind (e.g. 'workplace') is
  in its `type` property. Filter a kind with `e.type = '...'`, using the `types=[...]`
  values in the GRAPH SCHEMA; do not regex-match `entity_class` against a word from the
  question, and do not guess a value the schema and entity listing do not show.
- Return meaningful column aliases.
- Keep the query concise.
- If the question is present-tense ("who CAN approve…"), add a validity filter to
  timed nodes: ($asOf IS NULL OR ((n.valid_from IS NULL OR n.valid_from <= $asOf)
  AND (n.valid_to IS NULL OR n.valid_to > $asOf))); include "asOf" in parameters.
  For historical questions ("what WAS the limit in 2024?") set asOf to that date.
  Note this "asOf" is a floor (in force BY that date), not a point-in-time snapshot —
  valid_to is rarely set, so a still-open row satisfies any asOf at or after its
  valid_from.
- :DocumentHistory / :DocChunkHistory / :ObservationHistory are history only — the
  retired counterpart of :Document / :DocChunk / :Observation. Never traverse into
  them to answer a "what is true now" question; the base labels are current by
  construction. Only match a History label when the question explicitly asks about
  retired, superseded, or historical content.
- For an unbounded transitive walk ("all descendants of X", "everyone under X",
  "X's whole reporting line", "chain of command"), this tool is the wrong fit: the
  dedicated `artmind query graph hierarchy` command bounds depth/limit, detects
  cycles, and already knows which rel types run child→parent vs parent→child.
  Bound any such path to `*1..3` here rather than leaving it unbounded.
- If the match could return many rows (no highly-selective filter in the WHERE
  clause), add an explicit ORDER BY on a stable field (e.g. a name or id) before
  returning. The caller truncates to a fixed row count — without an ORDER BY,
  which rows survive that cut is arbitrary and can change between runs.
{graph_rules}
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
    graph_mode: bool = False,
) -> dict:
    """Generate a Cypher query from a natural-language question.

    `graph_mode` asks for nodes/relationships/paths (to be drawn) instead of
    table columns, and refuses `CALL`. Returns a dict with keys: cypher,
    parameters.
    """
    from artmind.graph_query import normalize_domains

    domains = normalize_domains(domains)

    env = load_env()
    resolved_model = resolve_llm_model(env, model)

    metadata = graph_metadata(domains)
    listing = entity_listing(domains)

    schema_info = _schema_summary(metadata)
    entities_info = _entities_summary(listing)

    warnings = _empty_domain_warnings(listing, domains)

    prompt = build_text2cypher_prompt(question, schema_info, entities_info, domains, graph_mode)
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
    if graph_mode:
        cypher = _prepare_graph_cypher(cypher)

    logger.info("text2cypher generated: {}", cypher)
    return {"cypher": cypher, "parameters": parameters, "warnings": warnings}


def execute_text2cypher(
    question: str,
    domains,
    model: str | None = None,
    dry_run: bool = False,
    max_rows: int = TEXT2CYPHER_MAX_ROWS,
) -> dict:
    """Generate and optionally execute a Cypher query from natural language.

    If dry_run is True, returns the generated Cypher without executing it.

    `rows` is capped to `max_rows` with `rows_total`/`truncated` alongside --
    see TEXT2CYPHER_MAX_ROWS's docstring for why the LLM's own query isn't
    trusted to bound itself.
    """
    from artmind.graph_query import _domain_output, normalize_domains

    domains = normalize_domains(domains)
    max_rows = int(max_rows)
    if max_rows < 0:
        raise ValueError("--maxRows must be >= 0")
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
        # No `rows` key: an empty list reads as "the query found nothing", and
        # agents took it that way (query-routing study P20). The CLI adds `next`.
        output["dry_run"] = True
        return output

    try:
        rows = strip_internal_props(_run_read_query(cypher, parameters))
        output["rows_total"] = len(rows)
        output["truncated"] = len(rows) > max_rows
        output["rows"] = rows[:max_rows]
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


# ── graph mode: drawing the result in Neo4j Browser ─────────────────────────

GRAPH_DEFAULT_LIMIT = 100

_CALL_RE = re.compile(r"\bCALL\b", re.IGNORECASE)
_LIMIT_RE = re.compile(r"\bLIMIT\b", re.IGNORECASE)
_UNION_RE = re.compile(r"\bUNION\b", re.IGNORECASE)
_PARAM_RE = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)")


def _prepare_graph_cypher(cypher: str) -> str:
    """Graph-mode guard: no `CALL` (procedures can't be vetted by keyword), and
    a LIMIT so the drawing stays legible. A LIMIT is appended only to a
    single-statement query; a UNION without one is left as the model wrote it."""
    if _CALL_RE.search(cypher):
        raise ValueError("Generated Cypher uses CALL, which graph view does not allow.")
    cypher = cypher.strip().rstrip(";").rstrip()
    if not _LIMIT_RE.search(cypher) and not _UNION_RE.search(cypher):
        cypher += f"\nLIMIT {GRAPH_DEFAULT_LIMIT}"
    return cypher


def cypher_literal(value) -> str:
    """Render a JSON-ish Python value as a Cypher literal.

    Strings are single-quoted with backslash and quote escaped; dict keys are
    backtick-quoted. Anything outside JSON's types (and non-finite floats) is
    refused rather than guessed at.
    """
    if value is None:
        return "null"
    if isinstance(value, bool):  # before int: bool is an int subclass
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"Cannot render {value!r} as a Cypher literal")
        return repr(value)
    if isinstance(value, str):
        escaped = value.replace("\\", "\\\\").replace("'", "\\'")
        escaped = escaped.replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")
        return f"'{escaped}'"
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(cypher_literal(v) for v in value) + "]"
    if isinstance(value, dict):
        items = []
        for k, v in value.items():
            key = str(k).replace("`", "``")
            items.append(f"`{key}`: {cypher_literal(v)}")
        return "{" + ", ".join(items) + "}"
    raise ValueError(f"Cannot render {type(value).__name__} as a Cypher literal")


def inline_parameters(cypher: str, parameters: dict) -> str:
    """Replace every `$name` in `cypher` with its literal value.

    Neo4j Browser has no way to supply parameters with a deep link, so the
    query must be self-contained to be pasted there. A `$name` with no entry in
    `parameters` raises: leaving it would make the Browser fail on a name the
    operator can't see the value of.
    """
    def sub(match: re.Match) -> str:
        name = match.group(1)
        if name not in parameters:
            raise ValueError(f"Cypher references ${name} but no value was generated for it")
        return cypher_literal(parameters[name])

    return _PARAM_RE.sub(sub, cypher)


_BOLT_SCHEMES = ("bolt", "bolt+s", "bolt+ssc", "neo4j", "neo4j+s", "neo4j+ssc")


def browser_deep_link(
    browser_url: str | None, cypher: str, connect_url: str | None = None
) -> str | None:
    """Neo4j Browser URL that pre-fills its editor with `cypher`.

    Browser does not auto-run from a URL; the operator presses Run. Returns
    None when no browser URL is configured. Only http(s) URLs are accepted, so
    a bad config value can't become a `javascript:` link in the UI.

    `connect_url` (the vault's bolt/neo4j URI) pre-fills Browser's login form
    through `dbms=`, so only the password is left to type. It carries no
    credentials; anything that is not a bolt/neo4j URI is left out.
    """
    if not browser_url or not browser_url.strip():
        return None
    parts = urlsplit(browser_url.strip())
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise ValueError(f"ARTMIND_KG_NEO4J_BROWSER_URL must be an http(s) URL, got {browser_url!r}")
    base = urlunsplit((parts.scheme, parts.netloc, parts.path or "/", "", ""))
    dbms = ""
    if connect_url:
        target = urlsplit(connect_url.strip())
        if target.scheme in _BOLT_SCHEMES and target.hostname and not target.username:
            dbms = f"dbms={quote(connect_url.strip(), safe='')}&"
    return f"{base}?{dbms}cmd=edit&arg={quote(cypher, safe='')}"
