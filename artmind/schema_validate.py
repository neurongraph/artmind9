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

    return errors


def validate_all(schemas_dir: Path = DOMAIN_SCHEMAS_DIR, meta_path: Path = DOMAIN_META_PATH) -> dict[str, list[str]]:
    """Validate every `*_schema.yaml` in `schemas_dir`. Returns {schema_name: [violations]}.

    Only schemas with at least one violation are included in the result.
    """
    meta = load_meta(meta_path)
    violations: dict[str, list[str]] = {}
    for schema_file in sorted(schemas_dir.glob("*_schema.yaml")):
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
