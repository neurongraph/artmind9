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


def graph_extraction_enabled(schema: dict | None) -> bool:
    """False only for a schema that says `graph_extraction: false`: its domain is
    chunks-only (DocChunks + embeddings, no entity extraction). The one reader
    of the key (spec 2026-10-30-chunks-only-domains-design.md)."""
    return (schema or {}).get("graph_extraction", True) is not False


def domain_graph_extraction(domain: str) -> bool:
    """`graph_extraction_enabled` for `domain`'s own schema file. A dotted child
    does not inherit its parent's flag; a missing or unreadable schema is a
    normal (graph) domain."""
    path = Path(DOMAIN_SCHEMAS_DIR) / f"{domain}_schema.yaml"
    try:
        schema = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return True
    return graph_extraction_enabled(schema if isinstance(schema, dict) else {})


def graph_extraction_warnings(schema: dict, schema_name: str = "") -> list[str]:
    """Keys a chunks-only schema declares that nothing will read."""
    if graph_extraction_enabled(schema):
        return []
    label = schema_name or schema.get("name", "<unnamed>")
    return [
        f"{label}: graph_extraction is false, so '{key}' is ignored (no entities are extracted)"
        for key in ("entity_types", "document_entity", "property_aliases") if schema.get(key)
    ]


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

    if "graph_extraction" in schema and not isinstance(schema["graph_extraction"], bool):
        errors.append(f"{label}.graph_extraction: must be true or false, not {schema['graph_extraction']!r}")

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

        identity = decl.get("identity")
        if identity is not None and identity not in IDENTITIES:
            errors.append(f"{where}: identity={identity!r} is not one of {list(IDENTITIES)}")

        errors.extend(_types_errors(decl, where))

        props = decl.get("properties") or {}
        for prop in props:
            _check_reserved(prop, prefix, f"{where}.properties", errors)
            tag = props[prop].get("temporal") if isinstance(props, dict) and isinstance(props[prop], dict) else None
            if tag is not None and tag not in TEMPORAL_VALUES:
                errors.append(f"{where}.properties.{prop}: temporal={tag!r} is not one of {list(TEMPORAL_VALUES)}")

        for target in (decl.get("relates_to") or {}):
            _check_reserved(target, prefix, f"{where}.relates_to", errors)

    errors.extend(_property_alias_errors(schema, entity_types, prefix, label))
    errors.extend(_temporal_document_errors(schema, label))
    errors.extend(_document_entity_errors(schema, entity_types, label))
    errors.extend(_property_decl_errors(entity_types, label))
    return errors


def class_types(decl: dict | None) -> list[str]:
    """The class's declared `type` values (spec 2026-11-15 §5.3): the closed `types:` list, else
    the illustrative `type_examples:`. The one reader; empty when neither is a list."""
    decl = decl if isinstance(decl, dict) else {}
    for key in ("types", "type_examples"):
        value = decl.get(key)
        if isinstance(value, list) and value:
            return [str(v) for v in value]
    return []


def _types_errors(decl: dict, where: str) -> list[str]:
    """`types:` is a closed list: non-empty, of names, and not beside `type_examples:`."""
    errors = []
    if "types" in decl:
        value = decl["types"]
        if not isinstance(value, list) or not value or not all(isinstance(v, str) and v.strip() for v in value):
            errors.append(f"{where}.types: must be a non-empty list of type names, e.g. [device, food]")
        if "type_examples" in decl:
            errors.append(f"{where}: declares both `types` and `type_examples`; keep `types` (the closed list)")
    return errors


#: What a property's `temporal:` tag may say (spec 2026-11-15 §4): the fact's valid time
#: (`valid_from`, `valid_to`), a deadline (`due`) or a review date (`review`).
TEMPORAL_VALUES = ("valid_from", "valid_to", "due", "review")
#: The keys of a schema's `temporal.document` label mapping and `temporal.document_patterns`.
DOCUMENT_TEMPORAL_KEYS = ("valid_from", "valid_to", "revised", "version")


def _temporal_document_errors(schema: dict, label: str) -> list[str]:
    """`temporal.document` / `document_patterns` keys outside DOCUMENT_TEMPORAL_KEYS, and a
    `temporal.document` value that is not a list of label strings (a bare string would be read
    one character at a time)."""
    temporal = schema.get("temporal")
    if not isinstance(temporal, dict):
        return []
    errors = []
    for section in ("document", "document_patterns"):
        block = temporal.get(section)
        if not isinstance(block, dict):
            continue
        for key, value in block.items():
            where = f"{label}.temporal.{section}.{key}"
            if key not in DOCUMENT_TEMPORAL_KEYS:
                errors.append(f"{where}: unknown key; expected one of {list(DOCUMENT_TEMPORAL_KEYS)}")
            elif section == "document" and not (
                    isinstance(value, list) and all(isinstance(v, str) for v in value)):
                errors.append(f"{where}: must be a list of label strings, not {value!r}")
    return errors


#: What a class's `identity:` may say (spec 2026-11-15 §5.3): same name is one entity
#: (`name`), one entity per day (`occasion`), or one shared label (`category`).
IDENTITIES = ("name", "occasion", "category")


def class_identity(schema: dict, entity_class: str) -> str:
    """What makes two same-name mentions one entity (spec 2026-11-15 §5.3). The one reader.

    Declared `identity`, else `name` for `kind: recurrent` and `occasion` otherwise -- an
    undeclared class is occurrent by `observations.class_kind`'s rule, so it is an occasion.
    """
    types = schema.get("entity_types") if isinstance(schema, dict) else None
    decl = types.get(entity_class) if isinstance(types, dict) else None
    decl = decl if isinstance(decl, dict) else {}
    declared = decl.get("identity")
    if declared in IDENTITIES:
        return declared
    return "name" if decl.get("kind") == "recurrent" else "occasion"


def classes_with_identity(schema: dict, *identities: str) -> set[str]:
    """The schema's declared classes whose `class_identity` is one of `identities`."""
    types = schema.get("entity_types") if isinstance(schema, dict) else None
    return {c for c in (types if isinstance(types, dict) else {}) if class_identity(schema, c) in identities}


def identity_warnings(schema: dict, schema_name: str = "") -> list[str]:
    """WARNs for `identity` (spec 2026-11-15 §5.3, plan question 20): an `occasion` class
    declared `kind: recurrent`, and a dated `kind: occurrent` class declared `identity: name`
    (its days fold into one entity) unless it is a `document_entity` class."""
    label = schema_name or schema.get("name", "<unnamed>")
    types = schema.get("entity_types")
    if not isinstance(types, dict):
        return []
    doc_setting = schema.get("document_entity")
    doc_classes = set((doc_setting.get("classes") or []) if isinstance(doc_setting, dict) else [])
    out = []
    for cls, decl in types.items():
        decl = decl if isinstance(decl, dict) else {}
        ident = class_identity(schema, cls)
        where = f"{label}.entity_types.{cls}"
        if ident == "occasion" and decl.get("kind") == "recurrent":
            out.append(f"{where}: identity: occasion with kind: recurrent -- its changes become "
                       "separate entities, never history")
        props = decl.get("properties")
        dated = any(isinstance(p, dict) and p.get("temporal") == "valid_from"
                    for p in (props.values() if isinstance(props, dict) else []))
        if ident == "name" and decl.get("kind") == "occurrent" and dated and cls not in doc_classes:
            out.append(f"{where}: a dated occurrent class with identity: name -- its mentions on "
                       "different days fold into one entity; declare identity: occasion")
    return out


def temporal_warnings(schema: dict, schema_name: str = "") -> list[str]:
    """WARNs for the temporal rules (spec 2026-11-15 §4, §5.3): an occasion class that
    declares no `temporal: valid_from` property is dated by its document only."""
    label = schema_name or schema.get("name", "<unnamed>")
    types = schema.get("entity_types")
    if not isinstance(types, dict):
        return []
    out = []
    for cls, decl in types.items():
        props = (decl or {}).get("properties") if isinstance(decl, dict) else None
        props = props if isinstance(props, dict) else {}
        tags = {p.get("temporal") for p in props.values() if isinstance(p, dict)}
        if class_identity(schema, cls) == "occasion" and "valid_from" not in tags:
            out.append(f"{label}.entity_types.{cls}: an occasion class declares no "
                       "`temporal: valid_from` property; its facts are dated by their document only")
    return out


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
    errors = [
        f"{where}.classes: {cls!r} is not a class of this schema's entity_types"
        for cls in setting["classes"] if cls not in entity_types
    ]
    labels = setting.get("date_labels")
    if labels is not None and (not isinstance(labels, list) or not all(isinstance(x, str) and x.strip() for x in labels)):
        errors.append(f"{where}.date_labels: must be a list of defined-term names, e.g. [Amendment Date, Effective Date]")
    return errors


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
        warnings = (reserved_relationship_warnings(schema, name) + graph_extraction_warnings(schema, name)
                    + property_alias_warnings(schema, name) + guidance_property_warnings(schema, name)
                    + own_key_property_warnings(schema, name) + temporal_warnings(schema, name)
                    + identity_warnings(schema, name))
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
        name = schema_key(schema_file, schema)
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


# ── Property checks (spec 2026-11-15 §2.5, §2.6) ─────────────────────────────

#: Keys every entity carries whatever its class declares (meta.yaml `entity_common_properties`).
COMMON_KEYS = frozenset({"name", "aliases", "entity_class", "type", "description", "context"})
#: Graph-side keys a view may read on any entity (plus `projection._PRESERVED_ENTITY_KEYS`).
_BASE_NODE_KEYS = frozenset({"key", "id", "entity_class", "embedding", "label"})
_DOTTED_KEY = re.compile(r"\bproperties\.([a-z][a-z0-9_]*)")
_EXAMPLE_KEY = re.compile(r'"([a-z][a-z0-9_]*)"\s*:')
_FIELD_REF = re.compile(r"\b([a-z][A-Za-z0-9_]*)\.([a-z][a-z0-9_]*)\b")
_LABEL_BIND = re.compile(r"\(\s*([a-z][A-Za-z0-9_]*)\s*((?::\s*[A-Za-z_][A-Za-z0-9_]*\s*)+)")
_LABEL = re.compile(r":\s*([A-Za-z_][A-Za-z0-9_]*)")
_CLASS_EQ = re.compile(
    r"\b([a-z][A-Za-z0-9_]*)\.entity_class\s*(?:=\s*'([A-Z0-9_]+)'|(?i:IN)\s*\[([^\]]*)\])")
_COMMENT = re.compile(r"'(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\"|//[^\n]*|/\*.*?\*/", re.S)


def schema_key(schema_file: Path, schema: dict | None = None) -> str:
    """The name `validate_all` keys a schema file by: its `name:`, else the file stem; a file
    that does not parse is keyed by its domain name (stem without `_schema`)."""
    if schema is None:
        if schema_parse_error(schema_file):
            return schema_file.name.removesuffix("_schema.yaml")
        schema = yaml.safe_load(schema_file.read_text(encoding="utf-8")) or {}
    return schema.get("name", schema_file.stem)


def family_schema(domain: str, schemas_dir: Path | None = None) -> dict:
    """The domain's schema with a family root's children merged in: a mart declaration or
    insights config on `banking` may name classes and properties `banking.policy` declares.
    A class several schemas declare gets the union of their properties (the first
    declaration of a key wins its definition)."""
    import paths
    from artmind import temporal

    directory = Path(schemas_dir) if schemas_dir is not None else Path(paths.DOMAIN_SCHEMAS_DIR)
    merged = dict(temporal.load_schema(domain, schemas_dir=directory) or {})
    types = {c: (dict(d) if isinstance(d, dict) else d) for c, d in (merged.get("entity_types") or {}).items()}
    if directory.is_dir():
        for f in sorted(directory.glob(f"{domain}.*_schema.yaml")):
            child = f.name[: -len("_schema.yaml")]
            for name, spec in ((temporal.load_schema(child, schemas_dir=directory) or {}).get("entity_types") or {}).items():
                have = types.get(name)
                if have is None:
                    types[name] = dict(spec) if isinstance(spec, dict) else spec
                elif isinstance(have, dict) and isinstance(spec, dict):
                    mine, theirs = have.get("properties"), spec.get("properties")
                    if isinstance(theirs, dict) and (mine is None or isinstance(mine, dict)):
                        have["properties"] = {**theirs, **(mine or {})}
    merged["entity_types"] = types
    return merged


def _declared(entity_types: dict) -> dict[str, set[str]]:
    """{class: declared keys} -- `observations.declared_property_keys`, the one reading."""
    from artmind.observations import declared_property_keys

    return declared_property_keys({"entity_types": entity_types})


def _property_decl_errors(entity_types: dict, label: str) -> list[str]:
    errors = []
    for cls, decl in entity_types.items():
        if not isinstance(decl, dict):
            continue
        props = decl.get("properties")
        if props is None:
            continue
        if not isinstance(props, dict):
            errors.append(f"{label}.entity_types.{cls}.properties: must be a map")
            continue
        for prop, pd in props.items():
            if not isinstance(pd, dict):
                continue
            where = f"{label}.entity_types.{cls}.properties.{prop}"
            if "required" in pd and not isinstance(pd["required"], bool):
                errors.append(f"{where}.required: must be true or false, not {pd['required']!r}")
            if "keep_reason" in pd and not (isinstance(pd["keep_reason"], str) and pd["keep_reason"].strip()):
                errors.append(f"{where}.keep_reason: must be a non-empty string")
    return errors


def property_alias_warnings(schema: dict, schema_name: str = "") -> list[str]:
    """An alias whose target no class declares (and is not a common key): under
    schema-only keys its values are dropped. An alias whose target is an observation's own field
    (`context`, `description`, ...): the renamed value is dropped too, since the flattened domain
    properties never carry an own field."""
    from artmind.observations import OBSERVATION_OWN_KEYS

    label = schema_name or schema.get("name", "<unnamed>")
    types = schema.get("entity_types") or {}
    declared = _declared(types if isinstance(types, dict) else {})
    every = set().union(*declared.values()) if declared else set()
    aliases = schema.get("property_aliases")
    if not isinstance(aliases, dict):
        return []  # a malformed map is an error from _property_alias_errors
    out = []
    for a, t in aliases.items():
        if str(t) in OBSERVATION_OWN_KEYS:
            out.append(f"{label}.property_aliases: {a!r} -> {t!r}, but {t!r} is an observation's own field, "
                       "never a domain property, so every value it renames is dropped")
        elif str(t) not in every and str(t) not in COMMON_KEYS:
            out.append(f"{label}.property_aliases: {a!r} -> {t!r}, but no class declares {t!r}, "
                       "so every value it renames is dropped")
    return out


def guidance_property_warnings(schema: dict, schema_name: str = "") -> list[str]:
    """Guidance or a `RIGHT:` example naming a key its class does not declare (spec §2.5).
    Domain `guidance.entities`/`.properties` is checked against every class; a class's own
    description/guidance against that class. Relationship guidance is not scanned. A
    `RIGHT:` example is read from its own line only (a multi-line example is not scanned)."""
    label = schema_name or schema.get("name", "<unnamed>")
    types = schema.get("entity_types") or {}
    if not isinstance(types, dict):
        return []
    declared = _declared(types)
    every = set().union(*declared.values()) if declared else set()
    guidance = schema.get("guidance") if isinstance(schema.get("guidance"), dict) else {}
    out = []
    for part in ("entities", "properties"):
        for key in sorted(set(_DOTTED_KEY.findall(str(guidance.get(part) or "")))):
            if key not in every and key not in COMMON_KEYS:
                out.append(f"{label}.guidance.{part}: names properties.{key}, which no class declares")
    for cls, decl in types.items():
        if not isinstance(decl, dict):
            continue
        text = f"{decl.get('description') or ''}\n{decl.get('guidance') or ''}"
        keys = set(_DOTTED_KEY.findall(text))
        keys |= {k for line in text.splitlines() if "RIGHT:" in line for k in _EXAMPLE_KEY.findall(line)}
        keys.discard("properties")  # `"properties": {...}` is the example's wrapper, not a key
        for key in sorted(keys - declared.get(cls, set()) - COMMON_KEYS):
            out.append(f"{label}.entity_types.{cls}: guidance names {key!r}, which {cls} does not declare")
    return out


def own_key_property_warnings(schema: dict, schema_name: str = "") -> list[str]:
    """A declared property named like an observation's own field (`context`, `domain`,
    `description`, ...): extraction writes that field itself, so the declaration never becomes a
    domain property. A WARN, not an error (several shipped schemas do it)."""
    from artmind.observations import OBSERVATION_OWN_KEYS

    label = schema_name or schema.get("name", "<unnamed>")
    types = schema.get("entity_types") or {}
    if not isinstance(types, dict):
        return []
    return [f"{label}.entity_types.{cls}.properties.{key}: collides with an observation field, "
            "so it is never stored as a property; rename it"
            for cls, keys in sorted(_declared(types).items()) for key in sorted(keys & OBSERVATION_OWN_KEYS)]


def meta_template_warnings(meta_path: Path = DOMAIN_META_PATH) -> list[str]:
    """The vault's meta.yaml still carries the old open-ended properties prompt (plan question 3)."""
    try:
        text = str((load_meta(meta_path).get("prompt_templates") or {}).get("properties") or "")
    except Exception:
        return []
    if "NO fixed schema" in text:
        return [f"{meta_path}: the properties prompt still invites keys the schema does not declare; "
                "run `artmind init --force` inside the vault to take the packaged meta.yaml"]
    return []


def _node_keys() -> frozenset:
    from artmind.projection import _PRESERVED_ENTITY_KEYS

    return _BASE_NODE_KEYS | {k for k in _PRESERVED_ENTITY_KEYS if not k.startswith("_")}


def _bound_variables(cypher: str, declared: dict[str, set[str]]) -> dict[str, set[str]]:
    """{variable: declared classes it is bound to} -- see `view_property_refs`."""
    from artmind.kg_views.model import _mask

    text = _mask(cypher)
    no_comments = _COMMENT.sub(lambda m: m.group() if m.group()[0] in "'\"" else " " * len(m.group()), cypher)
    bound: dict[str, set[str]] = {}
    for var, chain in _LABEL_BIND.findall(text):
        for cls in _LABEL.findall(chain):
            if cls in declared:
                bound.setdefault(var, set()).add(cls)
    for var, one, many in _CLASS_EQ.findall(no_comments):
        names = {one} if one else set(re.findall(r"'([A-Z0-9_]+)'", many))
        bound.setdefault(var, set()).update(n for n in names if n in declared)
    return bound


def view_bound_classes(cypher: str, declared: dict[str, set[str]]) -> set[str]:
    """Every declared class some variable of a view's Cypher is bound to."""
    return set().union(*_bound_variables(cypher, declared).values()) if cypher else set()


def view_property_refs(cypher: str, declared: dict[str, set[str]]) -> list[tuple[frozenset, str]]:
    """(classes a variable is bound to, key) for every `var.key` a view reads on a class-bound
    variable. A variable is bound by a label in its pattern (`(e:Entity:EVENT)`) or by
    `e.entity_class = 'X'` / `IN [...]`. Map projections (`e {.key}`) are not scanned."""
    from artmind.kg_views.model import _mask

    text = _mask(cypher)  # literals and comments blanked: 'a.b' inside a string is not a reference
    bound = _bound_variables(cypher, declared)
    node_keys = _node_keys()
    refs = []
    for var, key in sorted(set(_FIELD_REF.findall(text))):
        classes = bound.get(var)
        if classes and key not in COMMON_KEYS and key not in node_keys and not key.startswith("_"):
            refs.append((frozenset(classes), key))
    return refs


def mart_property_refs(decl) -> list[tuple[str, tuple[str, ...], str]]:
    """(label, classes, key) for every property a mart declaration reads; the key must be declared
    on at least one of `classes`. `decl` None gives []. Shared by `property_reference_report` and
    `property_audit.depended_on_keys`."""
    if decl is None:
        return []
    refs = []
    for label, cls, key in (("dimensions.person.seed", decl.person.entity_class, decl.person.seed_property),
                            ("dimensions.place.seed", decl.place.entity_class, decl.place.seed_property)):
        if key:
            refs.append((label, (cls,), key))
    for cls in decl.type.classes:
        refs.append(("dimensions.type.property", (cls,), decl.type.property))
    for kind, w in (decl.wholes or {}).items():
        for key in [m for m in (getattr(w, "match", None) or []) if m not in ("edge", "name")]:
            refs.append((f"wholes.{kind}.match", tuple(decl.happening_classes), key))
    return refs


def insights_property_refs(cfg) -> list[tuple[str, str, str]]:
    """(label, class, key) for every property an insights config's outcome / open_loops block reads."""
    if cfg is None:
        return []
    refs = []
    outcome, loops = cfg.block("outcome"), cfg.block("open_loops")
    if outcome:
        refs.append(("outcome.property", outcome["class"], outcome["property"]))
    if loops:
        for field in ("property", "urgency_property"):
            refs.append((f"open_loops.{field}", loops["class"], loops[field]))
    return refs


def property_reference_report(domain: str, schema: dict) -> dict:
    """Keys the mart declaration, insights config or a saved view reads that the schema does not
    declare (spec §2.6). `schema` is the domain's `family_schema`. Only `domains validate` calls
    this, never `setup`. A view is checked only for the domains its own spec names, so a
    family view is reported once. {"errors", "warnings"}."""
    from artmind.insights import config as insights_config
    from artmind.insights.model import InsightsError
    from artmind.kg_views import store as views_store
    from artmind.mart import declaration as mart_declaration

    types = schema.get("entity_types") or {}
    declared = _declared(types if isinstance(types, dict) else {})
    errors, warnings = [], []

    def has(cls, key):
        return key in COMMON_KEYS or key.startswith("_") or key in declared.get(cls, set())

    try:
        decl = mart_declaration.load_declaration(domain)
    except Exception:
        decl = None  # mart_validate already reports a broken declaration
    for label, classes, key in mart_property_refs(decl):
        if any(has(c, key) for c in classes):
            continue
        if label.startswith("wholes."):
            errors.append(f"mart/{domain} {label}: {key!r} is declared on none of {', '.join(classes)}")
        else:
            errors.append(f"mart/{domain} {label}: {classes[0]}.{key} is not declared")

    try:
        cfg = insights_config.load_config(domain)
    except InsightsError:
        cfg = None  # `insights config check` reports it
    for label, cls, key in insights_property_refs(cfg):
        if not has(cls, key):
            errors.append(f"insights/{domain} {label}: {cls}.{key} is not declared")

    views, _invalid = views_store.list_views([domain])
    for view in views:
        if domain not in view.spec.domains:
            continue  # checked under its own domain
        for classes, key in view_property_refs(view.spec.cypher, declared):
            if not any(key in declared[c] for c in classes):
                warnings.append(f"view:{view.spec.name} reads {'/'.join(sorted(classes))}.{key}, "
                                "which no class it scopes to declares")
    return {"errors": errors, "warnings": warnings}
