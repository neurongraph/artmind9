"""Temporal normalization: canonical valid-time / event-time properties.

Non-destructive: original domain-named properties are never touched; canonical
`valid_from`/`valid_to`/`event_at` are additive copies. Idempotent on re-run.
"""
import re
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger

from artmind.graph_query import neo4j_session
from paths import DOMAIN_SCHEMAS_DIR, MARKDOWNS_DIR

_MONTHS = {
    m.lower(): i for i, m in enumerate(
        ["January", "February", "March", "April", "May", "June", "July",
         "August", "September", "October", "November", "December"], start=1)
}
_ISO_FULL_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})")
_ISO_PARTIAL_RE = re.compile(r"^(\d{4})(?:-(\d{2}))?$")
_DMY_RE = re.compile(r"^(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})$")
_MDY_RE = re.compile(r"^([A-Za-z]+)\s+(\d{1,2}),?\s+(\d{4})$")


def parse_iso(value: str | None) -> str | None:
    """Deterministically parse a date-ish string to ISO-8601 (date), else None.

    Accepts full ISO, partial ISO (year / year-month), '15 March 2026',
    'March 15, 2026'. Returns the input unchanged for partial ISO.
    """
    if not value:
        return None
    v = value.strip()
    if _ISO_FULL_RE.match(v):
        return _ISO_FULL_RE.match(v).group(0)
    if _ISO_PARTIAL_RE.match(v):
        return v
    m = _DMY_RE.match(v)
    if m and m.group(2).lower() in _MONTHS:
        return f"{int(m.group(3)):04d}-{_MONTHS[m.group(2).lower()]:02d}-{int(m.group(1)):02d}"
    m = _MDY_RE.match(v)
    if m and m.group(1).lower() in _MONTHS:
        return f"{int(m.group(3)):04d}-{_MONTHS[m.group(1).lower()]:02d}-{int(m.group(2)):02d}"
    return None


def _find_header_value(md_text: str, labels: list[str]) -> str | None:
    """Find a label's value in the markdown body.

    Tries two formats, in order, since the real corpus uses the table form
    exclusively (verified against banking_document_corpus/policies/*.md —
    every document uses a "| Field | Value |" metadata table, never colon
    prose):
      1. Markdown table row: "| Label | Value |"
      2. Colon-delimited prose: "**Label:** value" / "Label: value"
    """
    for label in labels:
        table_pat = re.compile(
            r"^\|\s*" + re.escape(label) + r"\s*\|\s*([^|]+?)\s*\|",
            re.IGNORECASE | re.MULTILINE,
        )
        m = table_pat.search(md_text)
        if m:
            return m.group(1).strip().strip("*").strip()
        prose_pat = re.compile(
            r"^\**\s*" + re.escape(label) + r"\s*:\**\s*(.+?)\s*$",
            re.IGNORECASE | re.MULTILINE,
        )
        m = prose_pat.search(md_text)
        if m:
            return m.group(1).strip().strip("*").strip()
    return None


def lift_document_dates(md_text: str, frontmatter: dict, mapping: dict) -> dict:
    """Return canonical document props from header labels / frontmatter.

    mapping example: {"valid_from": ["Effective Date"], "version": ["Version"]}
    Records time_source: 'header' | 'frontmatter'.
    """
    out: dict = {}
    source = None
    for canon, labels in mapping.items():
        raw = _find_header_value(md_text, labels)
        if raw is not None:
            source = source or "header"
        else:
            for lbl in labels:
                if frontmatter.get(lbl.lower().replace(" ", "_")) or frontmatter.get(lbl):
                    raw = str(frontmatter.get(lbl.lower().replace(" ", "_")) or frontmatter.get(lbl))
                    source = source or "frontmatter"
                    break
        if raw is None and canon == "valid_from" and frontmatter.get("date"):
            raw = str(frontmatter["date"])
            source = source or "frontmatter"
        if raw is None:
            continue
        if canon == "version":
            out["version"] = raw
        else:
            iso = parse_iso(raw)
            out[canon] = iso if iso else raw
    if out:
        out["time_source"] = source or "header"
    return out


def load_schema(domain: str) -> dict:
    path = DOMAIN_SCHEMAS_DIR / f"{domain}_schema.yaml"
    import yaml
    return yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}


def canonical_entity_dates(entity: dict, schema_entities: dict, anchor: str | None) -> dict:
    """Map an entity's schema-declared date property to canonical props.

    schema_entities: {ENTITY_CLASS: {canonical_key: domain_property_name, ...}}
    Returns {} when the class has no temporal mapping or the value is absent.
    Deterministic parse only; unparseable values are left for the LLM leftover pass.
    """
    cls = entity.get("entity_class", "")
    mapping = schema_entities.get(cls)
    if not mapping:
        return {}
    props = entity.get("properties", entity)
    out: dict = {}
    unresolved: dict = {}
    for canon, domain_prop in mapping.items():
        raw = props.get(domain_prop)
        if raw is None:
            continue
        iso = parse_iso(str(raw))
        if iso:
            out[canon] = iso
        else:
            unresolved[canon] = str(raw)
    if out:
        out["time_source"] = "property"
    if unresolved:
        out["_unresolved"] = unresolved
        out["_anchor"] = anchor
    return out


def _temporal_mapping(schema: dict) -> tuple[dict, dict, str | None]:
    """Return (document_mapping, entities_mapping, relative_anchor) from a schema."""
    t = schema.get("temporal") or {}
    return t.get("document", {}), t.get("entities", {}), t.get("relative_anchor")


def normalize_time(domain: str, dry_run: bool = False) -> dict:
    """Backfill canonical temporal properties for every document in a domain.

    Additive + idempotent. Reads each Document's markdown for header dates and
    each Entity's schema-mapped property. Returns counts (deterministic vs llm).
    """
    schema = load_schema(domain)
    doc_map, ent_map, anchor = _temporal_mapping(schema)
    stats = {"domain": domain, "documents": 0, "entities": 0,
             "deterministic": 0, "llm": 0, "dry_run": dry_run}
    with neo4j_session() as session:
        docs = session.run(
            "MATCH (d:Document) WHERE d.domain = $domain RETURN d.id AS id, d.name AS name, d.path AS path",
            domain=domain,
        ).data()
        for doc in docs:
            md_file = MARKDOWNS_DIR / f"{Path(doc['name']).stem}.md"
            md_text, fm = "", {}
            if md_file.exists():
                from artmind.ingest import _parse_md_frontmatter
                fm, md_text = _parse_md_frontmatter(md_file.read_text(encoding="utf-8"))
            lifted = lift_document_dates(md_text, fm, doc_map) if doc_map else {}
            if lifted:
                stats["documents"] += 1
                stats["deterministic"] += 1
                if not dry_run:
                    session.run(
                        "MATCH (d:Document {id:$id}) SET d += $props, d.ingested_at = coalesce(d.ingested_at, $now)",
                        id=doc["id"], props=lifted,
                        now=datetime.now(timezone.utc).isoformat(),
                    )
        if ent_map:
            ents = session.run(
                "MATCH (e:Entity) WHERE e.domain = $domain RETURN e.id AS id, e.entity_class AS entity_class, properties(e) AS properties",
                domain=domain,
            ).data()
            for e in ents:
                canon = canonical_entity_dates(e, ent_map, anchor)
                clean = {k: v for k, v in canon.items() if not k.startswith("_")}
                if clean:
                    stats["entities"] += 1
                    stats["deterministic"] += 1
                    if not dry_run:
                        session.run(
                            "MATCH (e:Entity {id:$id}) SET e += $props",
                            id=e["id"], props=clean,
                        )
    logger.info("normalize_time({}): {}", domain, stats)
    return stats


def normalize_ingested_document(doc_kg_dir: Path, domain: str) -> dict:
    """Per-document normalization hook — runs after write_to_graph() at ingest time.

    Additive-only, idempotent, single-document scope; no dry-run gate.
    """
    import json
    schema = load_schema(domain)
    doc_map, ent_map, anchor = _temporal_mapping(schema)
    if not (doc_map or ent_map):
        return {"domain": domain, "skipped": "no temporal block"}
    try:
        document = json.loads((doc_kg_dir / "document.json").read_text(encoding="utf-8"))
        entities = json.loads((doc_kg_dir / "entities.json").read_text(encoding="utf-8"))
        properties_path = doc_kg_dir / "properties.json"
        properties_list = (
            json.loads(properties_path.read_text(encoding="utf-8")) if properties_path.exists() else []
        )
    except Exception as e:
        logger.warning("normalize_ingested_document: could not load JSON: {}", e)
        return {"domain": domain, "error": str(e)}
    # entities.json entries are flat (id/name/entity_class/...) with no "properties"
    # key — the domain-specific values (e.g. effective_date) live in properties.json,
    # keyed by entity id, and are merged onto the node only at Neo4j-write time
    # (see artmind.ingest._write_to_neo4j's props_by_id). Merge the same way here so
    # canonical_entity_dates sees the actual property values, not an empty dict.
    props_by_id = {p["id"]: p.get("properties", {}) for p in properties_list}
    md_file = MARKDOWNS_DIR / f"{Path(document['name']).stem}.md"
    md_text, fm = "", {}
    if md_file.exists():
        from artmind.ingest import _parse_md_frontmatter
        fm, md_text = _parse_md_frontmatter(md_file.read_text(encoding="utf-8"))
    lifted = lift_document_dates(md_text, fm, doc_map) if doc_map else {}
    written = {"documents": 0, "entities": 0}
    with neo4j_session() as session:
        if lifted:
            session.run(
                "MATCH (d:Document {id:$id}) SET d += $props, d.ingested_at = coalesce(d.ingested_at, $now)",
                id=document["id"], props=lifted, now=datetime.now(timezone.utc).isoformat(),
            )
            written["documents"] = 1
        for e in entities:
            entity_with_props = {**e, "properties": props_by_id.get(e["id"], {})}
            canon = canonical_entity_dates(entity_with_props, ent_map, anchor)
            clean = {k: v for k, v in canon.items() if not k.startswith("_")}
            if clean:
                session.run("MATCH (e:Entity {id:$id}) SET e += $props", id=e["id"], props=clean)
                written["entities"] += 1
    logger.info("normalize_ingested_document({}): {}", document.get("name"), written)
    return {"domain": domain, **written}


_SUPERSEDES_VER_RE = re.compile(
    r"supersedes?.*?Version\s+([0-9.]+)(\s*\([^)]*\))?", re.IGNORECASE
)
_EFFECTIVE_RE = re.compile(
    r"effective(?:\s+Date)?[:\s]+([0-9]{4}-[0-9]{2}-[0-9]{2}|\d{1,2}\s+[A-Za-z]+\s+\d{4})",
    re.IGNORECASE,
)
_NOTICE_SECTION_RE = re.compile(r"##\s*Supersession Notice\s*\n(.*?)(?=\n##|\n---|\Z)", re.IGNORECASE | re.DOTALL)


def parse_supersession_notice(md_text: str) -> dict | None:
    """Parse an explicit 'Supersession Notice' section for the superseded version + effective date.

    Scoped to the "## Supersession Notice" section when present, NOT the whole
    document body (see module docstring context on the Metadata-table bug this
    guards against). The superseded version's OWN date is typically given in a
    parenthetical immediately following its "Version X" mention (e.g. "Version 2.0
    (Effective Date: 2026-01-15)") — that span is excluded before searching for
    the effective date, so the search isn't fooled by which one merely comes first
    in reading order (the new document's effective date can appear before OR after
    the "supersedes" clause depending on phrasing).
    """
    section_match = _NOTICE_SECTION_RE.search(md_text)
    scope = section_match.group(1) if section_match else md_text
    m = _SUPERSEDES_VER_RE.search(scope)
    if not m:
        return None
    if m.group(2):
        excl_start, excl_end = m.span(2)
    else:
        excl_start = excl_end = m.end(1)
    search_scope = scope[:excl_start] + scope[excl_end:]
    eff = None
    em = _EFFECTIVE_RE.search(search_scope)
    if em:
        eff = parse_iso(em.group(1))
    return {"superseded_version": m.group(1).strip(), "effective": eff}


def apply_supersession(
    newer_doc_id: str,
    older_doc_id: str,
    scope: str = "document",
    effective: str | None = None,
    detected_by: str = "manual",
) -> dict:
    """Create (:Document)-[:SUPERSEDES]->(:Document) and set valid_to on the older side.

    Document scope also stamps valid_to on the older document's chunks — this is
    what makes --asOf queries exclude stale content automatically. Idempotent.
    """
    with neo4j_session() as session:
        session.run(
            """
            MATCH (newer:Document {id:$newer}), (older:Document {id:$older})
            MERGE (newer)-[s:SUPERSEDES {scope:$scope}]->(older)
            SET s.effective=$effective, s.detected_by=$detectedBy
            SET older.valid_to = coalesce($effective, older.valid_to),
                older.superseded_by = newer.id
            """,
            newer=newer_doc_id, older=older_doc_id, scope=scope,
            effective=effective, detectedBy=detected_by,
        )
        if scope == "document" and effective:
            session.run(
                "MATCH (c:DocChunk {doc_id:$older}) SET c.valid_to = coalesce($effective, c.valid_to)",
                older=older_doc_id, effective=effective,
            )
    logger.info("supersession: {} supersedes {} (scope={}, effective={})", newer_doc_id, older_doc_id, scope, effective)
    return {"newer": newer_doc_id, "older": older_doc_id, "scope": scope, "effective": effective}


def detect_supersession(domain: str, dry_run: bool = False) -> dict:
    """Scan each document's markdown for an explicit Supersession Notice and apply it.

    Matches the superseded Version against another Document in the same domain
    (via lifted `version`). Additive; safe to re-run.
    """
    report: dict = {"domain": domain, "applied": [], "dry_run": dry_run}
    with neo4j_session() as session:
        docs = session.run(
            "MATCH (d:Document) WHERE d.domain=$domain RETURN d.id AS id, d.name AS name, d.version AS version",
            domain=domain,
        ).data()
    by_version: dict = {}
    for d in docs:
        if not d.get("version"):
            continue
        version = str(d["version"])
        existing = by_version.get(version)
        if existing is not None and existing["id"] != d["id"]:
            logger.warning(
                "supersession: version {!r} collision in domain {!r} between document {!r} ({}) and {!r} ({}); "
                "keeping the latter",
                version, domain, existing["name"], existing["id"], d["name"], d["id"],
            )
        by_version[version] = d
    for d in docs:
        md_file = MARKDOWNS_DIR / f"{Path(d['name']).stem}.md"
        if not md_file.exists():
            continue
        from artmind.ingest import _parse_md_frontmatter
        _, body = _parse_md_frontmatter(md_file.read_text(encoding="utf-8"))
        notice = parse_supersession_notice(body)
        if not notice:
            continue
        older = by_version.get(notice["superseded_version"])
        if not older or older["id"] == d["id"]:
            continue
        report["applied"].append({"newer": d["id"], "older": older["id"], "effective": notice["effective"]})
        if not dry_run:
            apply_supersession(d["id"], older["id"], "document", notice["effective"], detected_by="notice")
    return report
