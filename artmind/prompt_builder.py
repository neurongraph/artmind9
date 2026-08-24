"""Assemble extraction prompts at runtime from `domains/meta.yaml` (the shared
prose shell) plus a domain schema's structured `entity_types` map (the
per-class declarations).

Before the redesign, `entities_prompt`/`properties_prompt`/`relationships_prompt`
were hand-written prose stored verbatim in each `*_schema.yaml` -- one source of
truth duplicated three ways (the prompt itself, `harmonizer.py`'s regex-block
sync, `schema_reference.py`'s regex-block parse). Now the schema declares only
the content (kind, description, properties, relates_to); this module renders
that content into the same shape of prompt text extraction.py has always
produced, so `ingest.py`/`update.py` call sites are unaffected.

Two placeholder styles coexist in a template, deliberately:
  - `{{DOUBLE_BRACE}}` tokens are filled in HERE via plain string replacement.
  - `{text}` / `{entities_list}` (single brace) are left untouched -- they are
    filled in later, per chunk, by extraction.py's existing `build_*_prompt`.
str.format() is never used: the OUTPUT FORMAT section of every template
contains literal JSON `{` `}` that must survive unparsed.
"""
from __future__ import annotations

from functools import lru_cache

import yaml

from paths import DOMAIN_META_PATH


@lru_cache(maxsize=1)
def _load_meta_cached(meta_path_str: str) -> dict:
    from pathlib import Path

    return yaml.safe_load(Path(meta_path_str).read_text(encoding="utf-8")) or {}


def load_meta() -> dict:
    """Load and cache `domains/meta.yaml`. Cache keyed on path so tests using a
    different ARTMIND_HOME/meta.yaml don't see a stale module-level value."""
    return _load_meta_cached(str(DOMAIN_META_PATH))


def _fill(template: str, tokens: dict[str, str]) -> str:
    out = template
    for key, value in tokens.items():
        out = out.replace("{{" + key + "}}", value)
    return out


def _class_enum(entity_types: dict) -> str:
    return " | ".join(entity_types.keys())


def _render_class_block(cls: str, decl: dict, meta: dict | None = None) -> str:
    """One class's block in the entities prompt.

    The class's declared `kind` is rendered as a naming instruction, from
    `meta.yaml`'s `kind_naming_rules`. Without it the recurrent naming rule
    existed only as prose for schema authors and never reached the extractor
    -- which is why names arrived carrying the very measurements and dates the
    aggregate key then had to strip back off.

    The rule is emitted BEFORE the class's own `guidance`, so a schema whose
    guidance says something more specific still wins the last word.
    """
    lines = [cls]
    desc = (decl.get("description") or "").strip()
    if desc:
        lines.append(f"  {desc}")
    kind_rule = ((meta or {}).get("kind_naming_rules") or {}).get(decl.get("kind"))
    if kind_rule:
        lines.append(f"  {kind_rule.strip()}")
    guidance = (decl.get("guidance") or "").strip()
    if guidance:
        lines.append(f"  {guidance}")
    type_examples = decl.get("type_examples") or []
    if type_examples:
        lines.append("  example type values: " + " | ".join(type_examples))
    return "\n".join(lines)


def _render_guidance_section(guidance: str | None, heading: str) -> str:
    """Render an optional domain-wide guidance block, or '' if absent.

    Rendered as its own section between the universal numbered rules and the
    next banner, so a domain's judgment calls (e.g. "capture every rate tier
    for each product") sit alongside -- not mixed into -- the universal rules.
    """
    text = (guidance or "").strip()
    if not text:
        return ""
    return f"\n{heading}\n{text}\n"


def _render_vocabulary_section(vocabulary: list | None) -> str:
    """The retrieved name vocabulary, or '' when there is none.

    Retrieval-gated rather than exhaustive: an ANN over `entity_embedding`
    supplies the ~25 nearest names in RECURRENT classes only (see
    `artmind.canonicalize`). Showing the extractor names already in use is
    what stops a new document coining a fresh one for something the graph
    already knows -- the cross-document half of the drift problem.
    """
    from artmind.canonicalize import render_vocabulary

    rendered = render_vocabulary(vocabulary or [])
    if not rendered:
        return ""
    return (
        "\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "NAMES ALREADY IN USE — REUSE THESE WHEN THEY FIT:\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "If an entity you find is one of these, use the existing name VERBATIM\n"
        "rather than coining a new one. If it is genuinely different, name it\n"
        "freshly — do not force a match.\n\n"
        f"{rendered}\n"
    )


def assemble_entities_prompt(
    schema: dict, meta: dict | None = None, vocabulary: list | None = None
) -> str:
    meta = meta if meta is not None else load_meta()
    entity_types = schema.get("entity_types") or {}
    class_blocks = "\n\n".join(_render_class_block(c, d, meta) for c, d in entity_types.items())
    tokens = {
        "SUBJECT": schema.get("subject") or schema.get("description", "")[:120],
        "PERSONA": schema.get("persona") or meta.get("default_persona", "a subject-matter analyst"),
        "CLASS_BLOCKS": class_blocks,
        "CLASS_ENUM": _class_enum(entity_types),
        "NAME_VOCABULARY": _render_vocabulary_section(vocabulary),
        "GUIDANCE": _render_guidance_section(
            (schema.get("guidance") or {}).get("entities"), "DOMAIN-SPECIFIC GUIDANCE:"
        ),
    }
    return _fill(meta["prompt_templates"]["entities"], tokens)


def _render_property_block(cls: str, decl: dict) -> str:
    properties = decl.get("properties") or {}
    if not properties:
        return ""
    lines = [f"For {cls}, consider:"]
    for name, prop_decl in properties.items():
        hint = (prop_decl or {}).get("hint", "") if isinstance(prop_decl, dict) else ""
        lines.append(f"  - {name}" + (f" ({hint})" if hint else ""))
    guidance = (decl.get("guidance") or "").strip()
    if guidance:
        lines.append(f"  {guidance}")
    return "\n".join(lines)


def assemble_properties_prompt(schema: dict, meta: dict | None = None) -> str:
    meta = meta if meta is not None else load_meta()
    entity_types = schema.get("entity_types") or {}
    blocks = [
        _render_property_block(cls, decl)
        for cls, decl in entity_types.items()
        if decl.get("properties")
    ]
    tokens = {
        "SUBJECT": schema.get("subject") or schema.get("description", "")[:120],
        "PROPERTY_BLOCKS": "\n\n".join(blocks),
        "GUIDANCE": _render_guidance_section(
            (schema.get("guidance") or {}).get("properties"), "DOMAIN-SPECIFIC GUIDANCE:"
        ),
    }
    return _fill(meta["prompt_templates"]["properties"], tokens)


def relationship_pairs(entity_types: dict) -> list[tuple[str, str, list[str]]]:
    """Flatten every class's relates_to into (a, b, [rel_type, ...]) rows.

    Declared from one side only by convention; a defensive dedupe collapses
    the rare case where both sides of a pair declare it (e.g. a harmonized
    child schema pulling in both parent classes independently), merging their
    rel_type lists rather than emitting the pair twice.
    """
    seen: dict[frozenset, tuple[str, str, list[str]]] = {}
    for cls, decl in entity_types.items():
        for target, rel_types in (decl.get("relates_to") or {}).items():
            key = frozenset((cls, target))
            if key in seen:
                a, b, existing = seen[key]
                merged = existing + [t for t in rel_types if t not in existing]
                seen[key] = (a, b, merged)
            else:
                seen[key] = (cls, target, list(rel_types))
    return list(seen.values())


def _render_relationship_block(a: str, b: str, rel_types: list[str]) -> str:
    return f"- {a} → {b}: " + ", ".join(rel_types)


def assemble_relationships_prompt(schema: dict, meta: dict | None = None) -> str:
    meta = meta if meta is not None else load_meta()
    entity_types = schema.get("entity_types") or {}
    pairs = relationship_pairs(entity_types)
    blocks = "\n".join(_render_relationship_block(a, b, types) for a, b, types in pairs)
    tokens = {
        "SUBJECT": schema.get("subject") or schema.get("description", "")[:120],
        "RELATIONSHIP_BLOCKS": blocks,
        "GUIDANCE": _render_guidance_section(
            (schema.get("guidance") or {}).get("relationships"), "DOMAIN-SPECIFIC GUIDANCE:"
        ),
    }
    return _fill(meta["prompt_templates"]["relationships"], tokens)
