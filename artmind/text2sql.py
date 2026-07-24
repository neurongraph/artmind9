import re

import duckdb
from loguru import logger

from artmind.extraction import call_llm, parse_json_response
from artmind.structured import registry as structured_registry
from artmind.structured.duckdb_adapter import DuckDBDatasource
from utils.functions import load_env, resolve_llm_model


# DuckDB write/side-effect verbs. This is the single shared source of truth for
# read-only SQL validation across `db sql` (CLI), the admin-UI's
# POST /api/structured/sql route, and text2sql's own generated queries — do not
# duplicate this list elsewhere. INSTALL/LOAD are included because DuckDB's
# extension loading is a side-effect (potential RCE/network access), not a read.
# EXPORT (as in EXPORT DATABASE) is COPY's multi-table sibling — arbitrary-path
# filesystem write. CHECKPOINT forces a WAL flush to disk. SET mutates
# session/global config (memory limits, temp/home dirs, etc.) — none of these
# are reads either.
_SQL_WRITE_VERBS_RE = re.compile(
    r"\b(INSERT|UPDATE|DELETE|CREATE|DROP|ALTER|TRUNCATE|COPY|ATTACH|DETACH"
    r"|PRAGMA|CALL|INSTALL|LOAD|SET|EXPORT|CHECKPOINT)\b",
    re.IGNORECASE,
)


def validate_read_only_sql(sql: str) -> None:
    """Raise ValueError if the SQL contains a write/side-effect operation."""
    match = _SQL_WRITE_VERBS_RE.search(sql)
    if match:
        raise ValueError(
            f"SQL contains a write/side-effect operation ({match.group()!r}) "
            "and cannot be executed. Only read-only queries are allowed."
        )


def _schema_summary_sql(
    tables: list[dict],
    columns_by_table: dict,
    mappings_by_table: dict,
) -> str:
    """Format registered tables/columns/mappings into a compact text block for the prompt.

    This is the same information ``db schema`` returns, condensed for an LLM:
    ``TABLE <name> (domain=<d>, rows=N): col:dtype [-> CLASS], ...`` per table.
    """
    if not tables:
        return "  (no tables registered)"

    lines: list[str] = []
    for t in tables:
        table_id = t["id"]
        name = t["table_name"]
        domain = t.get("domain", "")
        row_count = t.get("row_count")
        columns = columns_by_table.get(table_id, [])
        mappings = mappings_by_table.get(table_id, [])

        confirmed_classes: dict[str, list[str]] = {}
        for m in mappings:
            if m.get("confirmed"):
                confirmed_classes.setdefault(m["column"], []).append(m["entity_class"])

        col_strs = []
        for c in columns:
            col_str = f"{c['name']}:{c['dtype']}"
            classes = confirmed_classes.get(c["name"])
            if classes:
                col_str += f" [→ {', '.join(classes)}]"
            col_strs.append(col_str)

        lines.append(
            f"  TABLE {name} (domain={domain}, rows={row_count}): {', '.join(col_strs)}"
        )
    return "\n".join(lines)


def build_text2sql_prompt(
    question: str, schema_info: str, domains: list[str], as_of: str | None = None
) -> str:
    domains_str = ", ".join(domains)
    as_of_line = (
        f"- The user's as-of date is {as_of} — if a table has _valid_from/_valid_to or a"
        f" _current view, use {as_of} as the actual date value in your filter/view choice.\n"
        if as_of
        else ""
    )
    return f"""\
You are a DuckDB SQL expert. Given the table schema below, write a READ-ONLY
SQL query that answers the user's question.

RULES:
- The query MUST be read-only. Never use INSERT, UPDATE, DELETE, CREATE, DROP,
  ALTER, TRUNCATE, COPY, ATTACH, DETACH, PRAGMA, CALL, INSTALL, LOAD, SET,
  EXPORT, or CHECKPOINT.
- Use ONLY the tables and columns listed in the schema below — do not invent
  table or column names.
- The tables listed are already scoped to the requested domain(s): {domains_str}.
  You do not need to add extra domain filtering; just query the listed tables.
- Columns annotated "[→ CLASS]" are confirmed mappings to knowledge-graph
  entity classes — useful context for reasoning about the data, not a syntax
  requirement.
- Some tables may expose a `<table>_current` view or `valid_from`/`valid_to`
  columns for temporal (SCD-2) history. If the user supplies an as-of date,
  prefer the `_current` view or filter with
  `valid_from <= asOf AND (valid_to IS NULL OR valid_to > asOf)`; this wiring
  is forward-compat for tables that carry temporal history and may not be
  present on every table yet.
{as_of_line}- Return meaningful column aliases.
- Keep the query concise.

TABLE SCHEMA (domains: {domains}):
{schema_info}

USER QUESTION:
{question}

Respond with ONLY a JSON object (no markdown fencing, no explanation) with these keys:
- "sql": the SQL query string
- "notes": any short notes about assumptions made (empty string if none)
"""


def generate_sql(
    question: str,
    domains,
    model: str | None = None,
    as_of: str | None = None,
) -> dict:
    """Generate a SQL query from a natural-language question.

    Returns a dict with keys: sql, notes.
    """
    from artmind.graph_query import normalize_domains

    domains = normalize_domains(domains)

    env = load_env()
    resolved_model = resolve_llm_model(env, model)

    tables = structured_registry.list_tables(domains)
    columns_by_table = {t["id"]: structured_registry.get_columns(t["id"]) for t in tables}
    mappings_by_table = {t["id"]: structured_registry.list_mappings(t["id"]) for t in tables}

    schema_info = _schema_summary_sql(tables, columns_by_table, mappings_by_table)

    prompt = build_text2sql_prompt(question, schema_info, domains, as_of=as_of)
    raw = call_llm(resolved_model, prompt)
    parsed = parse_json_response(raw)

    if isinstance(parsed, list):
        parsed = parsed[0] if parsed else {}

    sql = parsed.get("sql", "")
    notes = parsed.get("notes", "")

    if not sql:
        raise ValueError("LLM did not return a SQL query")

    validate_read_only_sql(sql)

    logger.info("text2sql generated: {}", sql)
    return {"sql": sql, "notes": notes}


def execute_text2sql(
    question: str,
    domains,
    model: str | None = None,
    dry_run: bool = False,
    as_of: str | None = None,
) -> dict:
    """Generate and optionally execute a SQL query from natural language.

    If dry_run is True, returns the generated SQL without executing it.
    ``as_of``, when set, is threaded into the prompt (``build_text2sql_prompt``)
    so the LLM sees the concrete date value to filter/view by for tables that
    carry SCD-2 temporal history; it's also echoed back in the output. No
    additional filtering is applied on the Python side — the generated SQL
    itself is expected to carry the as-of predicate.
    """
    from artmind.graph_query import _domain_output, normalize_domains

    domains = normalize_domains(domains)
    result = generate_sql(question, domains, model, as_of=as_of)
    sql = result["sql"]

    output: dict = {
        **_domain_output(domains),
        "query_type": "sql",
        "command": "text2sql",
        "question": question,
        "generated_sql": sql,
        "notes": result.get("notes", ""),
    }
    if as_of:
        output["asOf"] = as_of

    if dry_run:
        output["rows"] = []
        output["dry_run"] = True
        return output

    # Isolated, ephemeral, domain-scoped connection — NOT the shared persistent
    # catalog. That catalog carries a permanent view for every table ever
    # ingested (across every domain); ``ensure_views(list_tables(domains))``
    # against it would still leave out-of-domain tables' views reachable from
    # an earlier ingest. An in-memory connection starts with no views at all,
    # so only the tables explicitly listed here are queryable.
    ds = DuckDBDatasource.in_memory()
    ds.ensure_views(structured_registry.list_tables(domains))
    try:
        output["rows"] = ds.run_sql(sql)
    except duckdb.Error as exc:
        raise ValueError(
            f"Generated SQL failed ({exc.__class__.__name__}): {exc}\n"
            f"SQL: {sql}"
        ) from exc
    return output
