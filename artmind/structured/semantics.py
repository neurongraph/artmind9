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
