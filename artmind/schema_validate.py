"""The meta-schema validator.

Loads `domains/meta.yaml` (the contract) and checks every `domains/schemas/
*_schema.yaml` against it -- run by `init` (setup.setup_all) and by
`domains harmonize`, and **failing loudly**: a schema missing a mandatory
`kind` is a bug in that schema, not something to silently default around.

See CONTEXT.md's "Meta-schema" entry and docs/redesign-phase-plan.md's
Phase 1 for what this contract is and why it exists.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

from paths import DOMAIN_META_PATH, DOMAIN_SCHEMAS_DIR


class SchemaValidationError(Exception):
    """Raised when one or more domain schemas violate the meta-schema."""


# SUPERSEDES is created ONLY by the audited temporal helpers (apply_supersession /
# apply_node_supersession), which stamp provenance (scope/detected_by/effective) and
# retire the older side. An LLM-extracted Entity->Entity relationship must never mint
# a bare SUPERSEDES edge with no provenance -- that corrupts lineage silently.
#
# EXTRACTED_FROM is reserved too: no shipped domain schema lists it as a legitimate
# Entity<->Entity rel_type (it's structural, written only between an :Observation and
# its source :DocChunk -- see observations.py -- never between two Entities), so
# blocking it as an Entity->Entity type is defense-in-depth with no known cost.
#
# PRIOR_STATE, entity_history.py and the :EntityVersion zone it backed are gone
# (Phase 4) -- nothing left links a live Entity to a history snapshot node, since
# observations now carry that role directly (see `query entity-history`). Not
# un-reserved: an LLM-minted PRIOR_STATE edge would still be confusing machinery-
# shaped noise even with nothing left to check it against.
#
# PART_OF is deliberately NOT reserved: multiple shipped schemas (general_schema,
# banking.organization_schema, sales_collateral_schema, project_governance_schema)
# list part_of as a legitimate LLM-extractable Entity->Entity relationship (e.g.
# "Branch X part_of Region Y"). The only structural PART_OF edge is the hardcoded
# DocChunk->Document edge written elsewhere in ingest.py's own upsert code -- a
# different code path from the Entity->Entity loop -- so reserving PART_OF here
# would silently drop legitimate, schema-sanctioned extractions.
#
# Defined here, not in ingest.py, so the schema validator can use it without
# importing the ingestion pipeline (ingest imports this module, via setup).
RESERVED_REL_TYPES = frozenset({
    "SUPERSEDES", "EXTRACTED_FROM", "PRIOR_STATE",
    # Phase 4: the collapsed relationship shape and the observation/entity
    # aggregation machinery that produces it. An extractor claiming one of
    # these as its own rel_type would be indistinguishable from the system's
    # own edges.
    "RELATES_TO", "ASSERTS_RELATION", "AGGREGATES",
})

_REL_SUGGESTIONS = {
    "SUPERSEDES": "replaces_{target}",
    "RELATES_TO": "associated_with",
}


def load_meta(meta_path: Path = DOMAIN_META_PATH) -> dict:
    if not meta_path.exists():
        raise SchemaValidationError(
            f"meta-schema not found at {meta_path} -- run 'artmind init' to seed it"
        )
    return yaml.safe_load(meta_path.read_text(encoding="utf-8")) or {}


def _check_reserved(key: str, prefix: str, where: str, errors: list[str]) -> None:
    if key.startswith(prefix):
        errors.append(f"{where}: {key!r} uses artmind's reserved {prefix!r} prefix")


# Mirrors `projection._sanitize_label` / `ingest._sanitize_label`'s exact
# transform -- every write to Neo4j sanitizes a class name this same way
# before using it as a dynamic label.
_RESERVED_STRUCTURAL_LABEL = "ENTITY"


def _sanitizes_to_reserved_label(cls: str) -> bool:
    """A class whose sanitized name is ENTITY would collide with the
    structural `:Entity` label every node already carries from its own
    `MERGE (e:Entity {_id: ...})` -- Neo4j labels are case-sensitive, so the
    two coexist rather than merging, and the node ends up visibly carrying
    what looks like the same label twice (general_schema.yaml's original
    generic-fallback class hit exactly this before being renamed to THING).
    Caught here, at schema-authoring time, rather than downstream in the
    write path -- a schema is the only place this can be prevented once."""
    sanitized = re.sub(r"[^A-Za-z0-9_]", "_", (cls or "").strip()).upper() or "UNKNOWN"
    return sanitized == _RESERVED_STRUCTURAL_LABEL


def validate_schema(schema: dict, meta: dict, schema_name: str = "") -> list[str]:
    """Return a list of violation messages; empty means the schema is valid.

    Checks, per the Phase 1 contract:
      - `entity_types` is a map (the pre-redesign list form is rejected)
      - every class declares `kind`, and it is one of `meta['kinds']`
      - no class name, property name, or relates_to target uses the reserved prefix
      - `property_aliases`, if present, is a flat {variant: canonical} map
    """
    errors: list[str] = []
    prefix = meta.get("reserved_prefix", "_")
    valid_kinds = set(meta.get("kinds", {}).keys())
    label = schema_name or schema.get("name", "<unnamed>")

    entity_types = schema.get("entity_types")
    if entity_types is None:
        return errors  # schemas with no entity_types at all are out of scope (e.g. pure stubs)
    if not isinstance(entity_types, dict):
        errors.append(
            f"{label}: entity_types must be a map of {{CLASS: {{...}}}}, not a list "
            "-- this is the pre-redesign format"
        )
        return errors

    for cls, decl in entity_types.items():
        where = f"{label}.entity_types.{cls}"
        _check_reserved(cls, prefix, f"{label}.entity_types", errors)
        if _sanitizes_to_reserved_label(cls):
            errors.append(
                f"{where}: class name sanitizes to the reserved label "
                f"'{_RESERVED_STRUCTURAL_LABEL}', which collides with the "
                f"structural :Entity label every node already carries -- "
                f"pick a more specific name (e.g. THING, SUBJECT, OTHER)"
            )
        if not isinstance(decl, dict):
            errors.append(f"{where}: class declaration must be a map")
            continue

        kind = decl.get("kind")
        if not kind:
            errors.append(f"{where}: missing mandatory 'kind' (one of {sorted(valid_kinds)})")
        elif kind not in valid_kinds:
            errors.append(f"{where}: kind={kind!r} is not one of {sorted(valid_kinds)}")

        for prop in (decl.get("properties") or {}):
            _check_reserved(prop, prefix, f"{where}.properties", errors)

        for target in (decl.get("relates_to") or {}):
            _check_reserved(target, prefix, f"{where}.relates_to", errors)

    errors.extend(_property_alias_errors(schema, entity_types, prefix, label))
    errors.extend(_document_entity_errors(schema, entity_types, label))
    return errors


def _document_entity_errors(schema: dict, entity_types: dict, label: str) -> list[str]:
    """`document_entity: {classes: [CLASS, ...]}` names the classes a document of
    this domain can itself be an instance of (`artmind.document_entity`). A class
    the schema does not declare would silently turn the step off for it."""
    if "document_entity" not in schema:
        return []
    setting = schema["document_entity"]
    where = f"{label}.document_entity"
    if not isinstance(setting, dict) or not isinstance(setting.get("classes"), list) or not setting["classes"]:
        return [f"{where}: must be a map with a non-empty 'classes' list, e.g. {{classes: [AGREEMENT]}}"]
    return [
        f"{where}.classes: {cls!r} is not a class of this schema's entity_types"
        for cls in setting["classes"] if cls not in entity_types
    ]


def _property_alias_errors(schema: dict, entity_types: dict, prefix: str, label: str) -> list[str]:
    """`property_aliases:` is `{variant: canonical}`, applied to every class
    (`observations.normalize_property_keys`). A declared key as a variant
    would rename a property some class asks for; a chain or a structural /
    reserved target is never what the author meant."""
    from artmind.observations import _STRUCTURAL_KEYS

    aliases = schema.get("property_aliases")
    if aliases is None:
        return []
    where = f"{label}.property_aliases"
    if not isinstance(aliases, dict):
        return [f"{where}: must be a map of {{variant: canonical_key}}"]
    declared = {
        str(p) for d in entity_types.values() if isinstance(d, dict) for p in (d.get("properties") or {})
    }
    errors: list[str] = []
    for variant, target in aliases.items():
        variant, target = str(variant), str(target)
        if variant in declared:
            errors.append(f"{where}: {variant!r} is a declared property -- it cannot also be an alias")
        if target in aliases:
            errors.append(f"{where}: {variant!r} -> {target!r} -> {aliases[target]!r} is a chain; "
                          f"map {variant!r} straight to the final key")
        if target.startswith(prefix) or target in _STRUCTURAL_KEYS or variant in _STRUCTURAL_KEYS:
            errors.append(f"{where}: {variant!r} -> {target!r} touches a structural/reserved key")
    return errors


def _rel_type_name(entry) -> str:
    """The rel_type a `relates_to` entry names, as the write path sees it: its
    leading word (an inline `(properties: ...)` hint is not part of the name),
    sanitized and upper-cased."""
    match = re.match(r"\s*([A-Za-z0-9_]+)", str(entry))
    return match.group(1).upper() if match else ""


def reserved_relationship_warnings(schema: dict, schema_name: str = "") -> list[str]:
    """One warning per `relates_to` entry whose rel_type artmind reserves.

    Not a validation error: shipped schemas declare some, and the extraction
    runs fine without those edges. But the write path drops every such
    relationship ("reserved rel_type skipped"), so the model is asked for edges
    that are then silently lost. The message names the class, the target and
    the relationship, lists every reserved name, and suggests a replacement."""
    label = schema_name or schema.get("name", "<unnamed>")
    reserved_list = ", ".join(sorted(r.lower() for r in RESERVED_REL_TYPES))
    warnings: list[str] = []
    for cls, decl in (schema.get("entity_types") or {}).items():
        if not isinstance(decl, dict):
            continue
        for target, rel_types in (decl.get("relates_to") or {}).items():
            for entry in rel_types or []:
                name = _rel_type_name(entry)
                if name not in RESERVED_REL_TYPES:
                    continue
                template = _REL_SUGGESTIONS.get(name)
                suggestion = (
                    f"e.g. {template.format(target=str(target).lower())!r}" if template
                    else "a name that says what the relationship is"
                )
                warnings.append(
                    f"{label}.entity_types.{cls}.relates_to.{target}: {str(entry).strip()!r} is a "
                    f"reserved relationship name -- artmind drops every extracted relationship of "
                    f"that type at write time, so this edge is never created. Use {suggestion}. "
                    f"Reserved: {reserved_list}."
                )
    return warnings


def warn_all(schemas_dir: Path = DOMAIN_SCHEMAS_DIR) -> dict[str, list[str]]:
    """`reserved_relationship_warnings` for every `*_schema.yaml` in `schemas_dir`
    that has any, as {schema_name: [warnings]}."""
    found: dict[str, list[str]] = {}
    for schema_file in sorted(schemas_dir.glob("*_schema.yaml")):
        if schema_parse_error(schema_file):
            continue  # reported as an error by validate_all
        schema = yaml.safe_load(schema_file.read_text(encoding="utf-8")) or {}
        name = schema.get("name", schema_file.stem)
        warnings = reserved_relationship_warnings(schema, name)
        if warnings:
            found[name] = warnings
    return found


def schema_parse_error(schema_file: Path) -> str | None:
    """Why `schema_file` is not valid YAML, naming the file, line and column and
    quoting the offending line -- or None when it parses. A syntax error used to
    surface as a bare PyYAML traceback in the middle of an ingest."""
    text = Path(schema_file).read_text(encoding="utf-8")
    try:
        yaml.safe_load(text)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        problem = getattr(exc, "problem", None) or str(exc).splitlines()[0]
        if mark is None:
            return f"{schema_file.name}: not valid YAML ({problem})"
        lines = text.splitlines()
        quoted = f": {lines[mark.line].strip()!r}" if mark.line < len(lines) and lines[mark.line].strip() else ""
        return (
            f"{schema_file.name}: not valid YAML at line {mark.line + 1}, column {mark.column + 1} "
            f"({problem}){quoted}"
        )
    return None


def validate_all(schemas_dir: Path = DOMAIN_SCHEMAS_DIR, meta_path: Path = DOMAIN_META_PATH) -> dict[str, list[str]]:
    """Validate every `*_schema.yaml` in `schemas_dir`. Returns {schema_name: [violations]}.

    Only schemas with at least one violation are included in the result. A file
    that is not valid YAML is a violation (`schema_parse_error`), keyed by its
    file stem, and does not stop the others being checked.
    """
    meta = load_meta(meta_path)
    violations: dict[str, list[str]] = {}
    for schema_file in sorted(schemas_dir.glob("*_schema.yaml")):
        parse_error = schema_parse_error(schema_file)
        if parse_error:
            violations[schema_file.name.removesuffix("_schema.yaml")] = [parse_error]
            continue
        schema = yaml.safe_load(schema_file.read_text(encoding="utf-8")) or {}
        name = schema.get("name", schema_file.stem)
        errors = validate_schema(schema, meta, schema_name=name)
        if errors:
            violations[name] = errors
    return violations


def validate_all_or_raise(schemas_dir: Path = DOMAIN_SCHEMAS_DIR, meta_path: Path = DOMAIN_META_PATH) -> None:
    """Raise SchemaValidationError with every violation if any schema fails."""
    violations = validate_all(schemas_dir, meta_path)
    if not violations:
        return
    lines = [f"meta-schema validation failed for {len(violations)} schema(s):"]
    for name, errors in violations.items():
        for e in errors:
            lines.append(f"  - {e}")
    raise SchemaValidationError("\n".join(lines))
