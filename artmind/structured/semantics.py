"""Propose a table's ``grain`` and its bridge columns with a single LLM call.

Distinct from ``mappings.py``, which is deliberately deterministic (exact +
difflib matching against KG entity names) because it answers a question the data
can answer: *do this column's values look like these entities?* Grain cannot be
settled that way. Whether rows assert rules or merely record facts is a semantic
judgement about meaning, and no shape heuristic separates a fee schedule from a
complaints log reliably. Guessing it wrong in the permissive direction is the
costly one: a `normative` table mistaken for `instance` silently skips the
quarantine rule.

Cadence differs from mappings for the same reason. ``propose_mappings`` re-runs
on every write because its answer genuinely tracks the data. Grain and
bridge_role describe what the table *means*, which does not change when rows
arrive, so ``pipeline.py`` calls this only on first registration -- never on a
replace refresh and never per SCD-2 batch. ``artmind db propose`` re-runs it on
demand, mirroring how ``db catalogue`` complements the automatic projection.

Confirmed values are never overwritten, matching ``propose_mappings``'s
guarantee: refreshing a table must not silently un-confirm an operator's review.
"""

import json

from artmind.structured import registry

#: Below this, a bridge-column proposal is not persisted. Matches
#: ``mappings.CONFIDENCE_FLOOR``'s role, and the same value for consistency.
CONFIDENCE_FLOOR = 0.4

#: Values a column's cells can play in fusion. Open vocabulary in the schema;
#: 'term' is the only one the retrieval path reads today.
BRIDGE_TERM = "term"

_PROMPT = """You are classifying one table from a structured data store that sits alongside a knowledge graph built from documents.

Answer two questions about it.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1. GRAIN — what do this table's rows denote?
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  instance   Particular real-world individuals or events, identified by a key.
             No document asserts them; they are records.
             e.g. customers, complaints, transactions, survey responses.

  lookup     Members of a controlled vocabulary or code list. Type-level, but
             no document asserts them either.
             e.g. sort codes, branch lists, country codes, status enums.

  normative  Rules, thresholds, obligations or entitlements — statements about
             what SHOULD be true, which a policy document also states or could
             state.
             e.g. fee schedules, interest rate cards, eligibility criteria,
             service-level targets.

The distinction that matters is normative vs the other two: only normative
content competes with what the documents say. If the rows record what IS,
they are instance or lookup. If they assert what OUGHT to be, they are
normative. When genuinely torn between instance and normative, choose
normative — it triggers a review rather than silently skipping one.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
2. BRIDGE COLUMNS — whose VALUES are worth searching the documents for?
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

A bridge column holds domain vocabulary a document might discuss, so its cell
values make useful search phrases against the graph.

  YES  vulnerability_driver = "Bereavement"  -> guidance exists on bereavement
       support_needed       = "Safe Space"   -> training covers safe spaces
       category             = "Mis-selling"  -> policy covers mis-selling

  NO   customer_id = "CUST-0019"   an identifier; means nothing to a document
       date_joined = "2026-01-14"  a date
       balance_gbp = 4210.55       a measure
       name        = "Sophie Ashworth"  a person's name, not domain vocabulary

Judge the sampled values, not the column name alone.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
TABLE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

name: {table_name}
domain: {domain}
row_count: {row_count}
refresh_mode: {refresh_mode}
business_key: {business_key}
effective_date_column: {effective_date_column}

columns:
{columns}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
OUTPUT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Return only this JSON object. No preamble, no explanation, no markdown fences.

{{
  "grain": "instance" | "lookup" | "normative",
  "grain_reason": string,
  "bridge_columns": [
    {{"column": string, "confidence": number between 0 and 1}}
  ]
}}
"""


def _column_lines(table_id: int) -> str:
    """Render each column as ``name (dtype) kind=… sample=[…]`` for the prompt."""
    lines = []
    for column in registry.get_columns(table_id):
        parts = [f"  - {column['name']} ({column['dtype']})"]
        if column.get("profile_json"):
            try:
                profile = json.loads(column["profile_json"])
            except (TypeError, ValueError):
                profile = {}
            if profile.get("kind"):
                parts.append(f"kind={profile['kind']}")
            sample = profile.get("distinct_sample") or []
            if sample:
                # Cap the sample: a high-cardinality column would otherwise
                # dominate the prompt without adding signal.
                shown = [str(v) for v in sample[:8]]
                parts.append(f"sample={shown}")
        lines.append(" ".join(parts))
    return "\n".join(lines) if lines else "  (no columns profiled)"


def build_prompt(table: dict, table_id: int) -> str:
    return _PROMPT.format(
        table_name=table["table_name"],
        domain=table["domain"],
        row_count=table.get("row_count"),
        refresh_mode=table.get("refresh_mode"),
        business_key=table.get("business_key") or "(none)",
        effective_date_column=table.get("effective_date_column") or "(none)",
        columns=_column_lines(table_id),
    )


def propose_semantics(table_id: int, model: str | None = None) -> dict:
    """Propose ``grain`` and bridge columns for ``table_id``. Persists both.

    Returns ``{"grain", "grain_reason", "grain_written", "bridge_columns"}``.
    ``grain_written`` is False when an operator has already confirmed a grain,
    in which case the proposal is reported but not applied.
    """
    from artmind.extraction import call_llm, parse_json_response
    from utils.functions import load_env, resolve_llm_model

    table = registry.get_table_by_id(table_id)
    if table is None:
        raise ValueError(f"no registered table with id {table_id}")

    resolved_model = resolve_llm_model(load_env(), model)
    raw = call_llm(resolved_model, build_prompt(table, table_id))
    parsed = parse_json_response(raw) or {}

    grain = parsed.get("grain")
    grain_reason = parsed.get("grain_reason") or ""
    grain_written = False
    if grain in registry.GRAINS:
        if table.get("grain_confirmed"):
            # An operator already ruled on this; report but do not overwrite.
            grain = table["grain"]
        else:
            registry.set_grain(table_id, grain, confirmed=False)
            grain_written = True
    else:
        grain = table["grain"]

    known_columns = {c["name"] for c in registry.get_columns(table_id)}
    already_confirmed = {
        role["column"] for role in registry.list_column_roles(table_id) if role.get("confirmed")
    }

    persisted = []
    for entry in parsed.get("bridge_columns") or []:
        if not isinstance(entry, dict):
            continue
        column = entry.get("column")
        try:
            confidence = float(entry.get("confidence", 0.0))
        except (TypeError, ValueError):
            continue
        # Guard against a hallucinated column name reaching the registry.
        if column not in known_columns:
            continue
        if confidence < CONFIDENCE_FLOOR:
            continue
        if column in already_confirmed:
            continue
        registry.upsert_column_role(table_id, column, BRIDGE_TERM, confidence, confirmed=False)
        persisted.append({"column": column, "bridge_role": BRIDGE_TERM, "confidence": confidence})

    return {
        "grain": grain,
        "grain_reason": grain_reason,
        "grain_written": grain_written,
        "bridge_columns": persisted,
    }


_MAPPING_PROMPT = """You are matching a structured table's columns to entity classes from a knowledge-graph domain schema.

For each column, judge whether its SAMPLED VALUES look like instances of one of the listed
entity classes below — based on the class's description, not by matching column/class names
literally. Judge the values the same way you would for bridge columns: a column full of a
bank's marketing names denotes the PRODUCT class if its values read like product names, even
if none of them has been seen in any ingested document yet.

A column can legitimately map to more than one class (e.g. a `category` column on a complaints
table might describe both a PRODUCT and an ISSUE_TYPE). It is fine — and expected — for a
column with no plausible class (an id, a date, a raw numeric measure) to be omitted entirely.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
TABLE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

name: {table_name}
domain: {domain}
row_count: {row_count}
refresh_mode: {refresh_mode}

columns:
{columns}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CANDIDATE ENTITY CLASSES (from the domain schema)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

{classes}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
OUTPUT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Return only this JSON object. No preamble, no explanation, no markdown fences.

{{
  "mappings": [
    {{"column": string, "entity_class": string, "confidence": number between 0 and 1}}
  ]
}}
"""


def _class_lines(classes: list[dict]) -> str:
    lines = []
    for c in classes:
        types = ", ".join(c.get("types") or [])
        suffix = f" (e.g. {types})" if types else ""
        lines.append(f"  - {c['class']}: {c['description']}{suffix}")
    return "\n".join(lines) if lines else "  (no entity classes found in schema)"


def build_mapping_prompt(table: dict, table_id: int, classes: list[dict]) -> str:
    return _MAPPING_PROMPT.format(
        table_name=table["table_name"],
        domain=table["domain"],
        row_count=table.get("row_count"),
        refresh_mode=table.get("refresh_mode"),
        columns=_column_lines(table_id),
        classes=_class_lines(classes),
    )


def propose_mapping(
    table_id: int, domain: str, model: str | None = None, *, only_columns: set[str] | None = None
) -> list[dict]:
    """Propose ``column -> entity_class`` mappings for ``table_id`` from
    ``domain``'s schema (no live-KG dependency). Persists via the same
    confidence floor and never-overwrite-confirmed guarantee ``propose_semantics``
    gives bridge columns.

    Raises ``ValueError`` if ``domain`` has no schema file (or no
    ``entities_prompt``) — a distinct, clearly-reported failure from "schema
    exists but has zero parseable classes," which returns ``[]`` instead.

    ``only_columns``, when given, additionally restricts *persistence* to that
    column-name set — used by a later replace-mode-refresh new-column trigger
    so an existing, unrelated column's mapping state is never touched just
    because the whole table was re-profiled.
    """
    from artmind.extraction import call_llm, parse_json_response
    from artmind.schema_reference import parse_entities
    from artmind.temporal import load_schema
    from utils.functions import load_env, resolve_llm_model

    table = registry.get_table_by_id(table_id)
    if table is None:
        raise ValueError(f"no registered table with id {table_id}")

    schema = load_schema(domain)
    entities_prompt = schema.get("entities_prompt")
    if not entities_prompt:
        raise ValueError(
            f"domain '{domain}' has no schema file (or no entities_prompt) — the mapping"
            " step needs the domain's entity schema to judge column classes against. Check"
            " domains/schemas/, or run 'artmind domains harmonize' if this is a dotted"
            " sub-domain."
        )
    classes = parse_entities(entities_prompt)
    if not classes:
        return []

    resolved_model = resolve_llm_model(load_env(), model)
    raw = call_llm(resolved_model, build_mapping_prompt(table, table_id, classes))
    parsed = parse_json_response(raw) or {}

    known_columns = {c["name"] for c in registry.get_columns(table_id)}
    known_classes = {c["class"] for c in classes}
    already_confirmed = {
        (m["column"], m["entity_class"])
        for m in registry.list_mappings(table_id)
        if m.get("confirmed")
    }

    persisted = []
    for entry in parsed.get("mappings") or []:
        if not isinstance(entry, dict):
            continue
        column = entry.get("column")
        entity_class = entry.get("entity_class")
        try:
            confidence = float(entry.get("confidence", 0.0))
        except (TypeError, ValueError):
            continue
        # Hallucination guard, mirrors propose_semantics's bridge_columns check.
        if column not in known_columns or entity_class not in known_classes:
            continue
        if only_columns is not None and column not in only_columns:
            continue
        if confidence < CONFIDENCE_FLOOR:
            continue
        if (column, entity_class) in already_confirmed:
            continue
        registry.upsert_mapping(table_id, column, entity_class, confidence, confirmed=False)
        persisted.append({"column": column, "entity_class": entity_class, "confidence": confidence})

    return persisted
