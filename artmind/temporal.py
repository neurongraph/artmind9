"""Temporal normalization: canonical valid-time / event-time properties.

Non-destructive: original domain-named properties are never touched; canonical
`valid_from`/`valid_to`/`event_at` are additive copies. Idempotent on re-run.
"""
import re
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger

from artmind.graph_query import expand_domain_family, neo4j_session
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


def lift_document_dates(md_text: str, frontmatter: dict, mapping: dict, defaults: dict | None = None) -> dict:
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
            # Strip trailing annotations like "1.0 (Updated Monthly)" down to the bare
            # numeric token — supersession notices cite a bare version ("Version 1.0")
            # and resolution matches by exact string equality, so an annotated value
            # would never match. Non-numeric version schemes are kept as-is.
            m = re.match(r"\s*(\d+(?:\.\d+)*)", raw)
            out["version"] = m.group(1) if m else raw.strip()
        else:
            iso = parse_iso(raw)
            out[canon] = iso if iso else raw
    if out:
        out["time_source"] = source or "header"
    elif defaults and defaults.get("valid_from") == "ingestion_date":
        out["valid_from"] = datetime.now(timezone.utc).date().isoformat()
        out["time_source"] = defaults.get("time_source", "default_ingestion")
        out["valid_from_inferred"] = defaults.get("valid_from_inferred", True)
    return out


def _deep_merge_temporal(parent: dict, child: dict) -> dict:
    """Merge a parent domain's `temporal:` block under a child's.

    Semantics (parent is the shared/default layer, child is the specific override):
      - defaults: parent as base, child overrides individual keys.
      - relative_anchor: child scalar wins if present, else parent's.
      - document: per-canonical-key union of label lists, parent-first, deduped.

    `entities` is deliberately absent here (Phase 1): per-class temporal mapping
    now lives on each class's own `properties` declarations (a property tagged
    `temporal: valid_from`), inherited the same way the rest of `entity_types`
    is -- via `domains harmonize`'s dict merge, not this runtime merge. See
    `_entity_temporal_mapping`.
    """
    merged: dict = {}

    defaults = {**(parent.get("defaults") or {}), **(child.get("defaults") or {})}
    if defaults:
        merged["defaults"] = defaults

    anchor = child.get("relative_anchor") or parent.get("relative_anchor")
    if anchor:
        merged["relative_anchor"] = anchor

    parent_doc = parent.get("document") or {}
    child_doc = child.get("document") or {}
    document: dict = {}
    for key in {*parent_doc, *child_doc}:
        p_vals = parent_doc.get(key, [])
        p_vals = p_vals if isinstance(p_vals, list) else [p_vals]
        c_vals = child_doc.get(key, [])
        c_vals = c_vals if isinstance(c_vals, list) else [c_vals]
        seen: list = []
        for v in [*p_vals, *c_vals]:
            if v not in seen:
                seen.append(v)
        document[key] = seen
    if document:
        merged["document"] = document

    return merged


def load_schema(domain: str) -> dict:
    """Load a domain's schema, deep-merging inherited `temporal:` from a dotted parent.

    For a leaf domain like `banking.policy`, also loads `banking_schema.yaml` and
    merges its `temporal:` block underneath the child's (see `_deep_merge_temporal`).
    Non-temporal keys (prompts, entity_types, etc.) are NOT inherited here — that
    composition happens at build time via `artmind domains harmonize`.
    """
    path = DOMAIN_SCHEMAS_DIR / f"{domain}_schema.yaml"
    import yaml
    if not path.exists():
        return {}
    schema = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if "." in domain:
        parent_name = domain.rsplit(".", 1)[0]
        parent_path = DOMAIN_SCHEMAS_DIR / f"{parent_name}_schema.yaml"
        if parent_path.exists():
            parent_schema = yaml.safe_load(parent_path.read_text(encoding="utf-8")) or {}
            schema["temporal"] = _deep_merge_temporal(
                parent_schema.get("temporal") or {}, schema.get("temporal") or {}
            )
    return schema


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


def _entity_temporal_mapping(schema: dict) -> dict:
    """Build {ENTITY_CLASS: {canonical_key: domain_property_name}} from each
    class's `properties` declarations.

    Post-redesign home for what used to live at `temporal.entities` (folded
    into the property declarations per Phase 1 -- e.g. `effective_date:
    {temporal: valid_from}` on PRODUCT). Same output shape as before, so
    `canonical_entity_dates` (the consumer) is unchanged.
    """
    mapping: dict = {}
    for cls, decl in (schema.get("entity_types") or {}).items():
        class_map = {}
        for prop, prop_decl in (decl.get("properties") or {}).items():
            canon = prop_decl.get("temporal") if isinstance(prop_decl, dict) else None
            if canon:
                class_map[canon] = prop
        if class_map:
            mapping[cls] = class_map
    return mapping


def _temporal_mapping(schema: dict) -> tuple[dict, dict, str | None]:
    """Return (document_mapping, entities_mapping, relative_anchor) from a schema."""
    t = schema.get("temporal") or {}
    return t.get("document", {}), _entity_temporal_mapping(schema), t.get("relative_anchor")








_SUPERSEDES_VER_RE = re.compile(
    r"supersedes?.*?Version\s+([0-9.]+)(\s*\([^)]*\))?", re.IGNORECASE
)
_EFFECTIVE_RE = re.compile(
    r"effective(?:\s+Date)?[:\s]+([0-9]{4}-[0-9]{2}-[0-9]{2}|\d{1,2}\s+[A-Za-z]+\s+\d{4})",
    re.IGNORECASE,
)
_NOTICE_SECTION_RE = re.compile(r"##\s*Supersession Notice\s*\n(.*?)(?=\n##|\n---|\Z)", re.IGNORECASE | re.DOTALL)
_TRAILING_NUMERIC_SUFFIX_RE = re.compile(r"_v?\d+$", re.IGNORECASE)
_WIKILINK_TARGET_RE = re.compile(r"\[\[([^\]|#]+)")


def _title_stem(name: str) -> str:
    """Normalize a document name to its title family for collision/candidate matching.

    Strips the extension and any trailing numeric or "_v<N>" suffixes (repeatedly) so
    that dated/versioned siblings of the same document family compare equal — e.g.
    interest_rate_schedule_2026, _2026_02, _2026_03 and policy_complaints, _v2, _v3
    all reduce to their shared family stem. Names with non-numeric suffixes (e.g. two
    distinct case-file exhibits that both happen to be version "1.0") are left distinct.
    """
    stem = Path(name).stem
    while True:
        m = _TRAILING_NUMERIC_SUFFIX_RE.search(stem)
        if not m:
            return stem
        stem = stem[:m.start()]


def parse_supersession_metadata_table(md_text: str) -> dict | None:
    """Parse an explicit metadata-table 'Supersedes' row as an alternate notice format.

    Some documents self-declare lineage via a `| Supersedes | [[doc_name]] |`
    metadata-table row (naming the superseded document directly) plus an
    `| Effective Date | ... |` row, instead of a prose "## Supersession Notice"
    section referencing a version number (see parse_supersession_notice). Returns
    the older document's name (not a version) so callers must resolve it by name.
    """
    raw = _find_header_value(md_text, ["Supersedes"])
    if not raw or raw.strip().lower() == "none":
        return None
    link = _WIKILINK_TARGET_RE.search(raw)
    doc_name = (link.group(1) if link else raw).strip()
    if not doc_name:
        return None
    effective = parse_iso(_find_header_value(md_text, ["Effective Date"]))
    return {"superseded_doc_name": doc_name, "effective": effective}


def parse_supersession_notice(md_text: str) -> dict | None:
    """Parse an explicit 'Supersession Notice' section for the superseded version + effective date.

    Strictly scoped to the "## Supersession Notice" section — if no such section
    exists, returns None immediately rather than falling back to scanning the
    whole document body. A whole-body fallback would let this prose-oriented
    regex match a metadata-table "| Supersedes | Version X, [[doc]] |" row
    instead (that shape belongs exclusively to parse_supersession_metadata_table,
    which handles it correctly, including the separate "| Effective Date | ... |"
    row that this function's colon/whitespace-based effective-date regex cannot
    parse out of a table cell). The superseded version's OWN date is typically
    given in a parenthetical immediately following its "Version X" mention (e.g.
    "Version 2.0 (Effective Date: 2026-01-15)") — that span is excluded before
    searching for the effective date, so the search isn't fooled by which one
    merely comes first in reading order (the new document's effective date can
    appear before OR after the "supersedes" clause depending on phrasing).
    """
    section_match = _NOTICE_SECTION_RE.search(md_text)
    if not section_match:
        return None
    scope = section_match.group(1)
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
    """Create (:Document)-[:SUPERSEDES]->(:Document) and retire the older side.

    Document-level lineage only. What it does NOT do any more is reach into the
    entity layer: `_retire_orphaned_entities` stamped `valid_to`,
    `superseded_by` and `status='superseded'` onto entities it guessed were
    solely sourced by the older document, using a `size(docIds) = 1` heuristic
    that failed silently — on the live corpus it fired on 2 of 5 supersessions
    and left 235 entities live.

    All three of those properties are projection-owned now and would be wiped
    by the next rebuild anyway. Instead, superseding a document **retires** it
    (`artmind.lifecycle`): its observations move to `history`, and any
    aggregate key left with zero `latest` observations loses its `:Entity`.
    Not a heuristic — an arithmetic fact about what is still asserted.

    Idempotent.
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
    if scope == "document":
        # Retirement is document-granular by nature: a whole document's
        # assertions stop being the current record at once.
        from artmind.lifecycle import retire_document

        retire_document(older_doc_id)
    logger.info("supersession: {} supersedes {} (scope={}, effective={})", newer_doc_id, older_doc_id, scope, effective)
    return {"newer": newer_doc_id, "older": older_doc_id, "scope": scope, "effective": effective}




def _read_doc_body(name: str) -> str | None:
    """Return the markdown body for a registered document, or None if absent."""
    md_file = MARKDOWNS_DIR / f"{Path(name).stem}.md"
    if not md_file.exists():
        return None
    from artmind.ingest import _parse_md_frontmatter
    _, body = _parse_md_frontmatter(md_file.read_text(encoding="utf-8"))
    return body


def _resolve_version_candidate(citing_doc: dict, version: str, by_version_group: dict) -> dict | None:
    """Resolve a "Supersedes ... Version X" reference to the Document that version names.

    A bare version string like "2.0" is not a reliable global key on its own — unrelated
    documents routinely share the same boilerplate version (see the collision warning in
    detect_supersession). When more than one document in the domain carries `version`,
    only pick the one that also shares the citing document's title family (_title_stem);
    an unresolved collision is skipped rather than guessed, since a silently wrong
    SUPERSEDES link is worse than a missed one.
    """
    candidates = [g for g in by_version_group.get(version, []) if g["id"] != citing_doc["id"]]
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]
    citing_stem = _title_stem(citing_doc["name"])
    stem_matches = [g for g in candidates if _title_stem(g["name"]) == citing_stem]
    if len(stem_matches) == 1:
        return stem_matches[0]
    logger.warning(
        "supersession: version {!r} ambiguous for {!r} ({}) — {} same-version candidates, "
        "none uniquely matching title family {!r}; skipping",
        version, citing_doc["name"], citing_doc["id"], len(candidates), citing_stem,
    )
    return None


def _infer_family_supersessions(
    docs: list[dict], only_doc_name: str | None
) -> list[tuple[dict, dict, str]]:
    """Infer supersession pairs among same-title-family documents by valid_from.

    Only documents carrying a canonical valid_from participate. Members of a
    family are sorted by valid_from and linked as a chain of consecutive pairs
    (each newer document supersedes its immediate predecessor). A tie on
    valid_from is skipped — ambiguity is never guessed. When only_doc_name is
    set, only pairs whose NEWER side is that document are returned (the
    commit-time per-document scope). Returns (newer, older, effective) tuples
    with effective = the newer document's valid_from.
    """
    families: dict[str, list[dict]] = {}
    for d in docs:
        if d.get("valid_from"):
            families.setdefault(_title_stem(d["name"]), []).append(d)
    pairs: list[tuple[dict, dict, str]] = []
    for group in families.values():
        if len(group) < 2:
            continue
        group.sort(key=lambda d: str(d["valid_from"]))
        for older, newer in zip(group, group[1:]):
            if str(older["valid_from"]) == str(newer["valid_from"]):
                continue
            if only_doc_name is not None and newer["name"] != only_doc_name:
                continue
            pairs.append((newer, older, str(newer["valid_from"])))
    return pairs


def detect_supersession(domain: str, dry_run: bool = False, only_doc_name: str | None = None) -> dict:
    """Scan each document's markdown for an explicit supersession notice and apply it.

    Recognizes three supersession signals:
      1. A prose "## Supersession Notice" section naming a superseded Version
         number (parse_supersession_notice) — resolved against another Document
         in the same domain via lifted `version`.
      2. A metadata-table `| Supersedes | [[doc_name]] |` row naming the older
         document directly (parse_supersession_metadata_table) — resolved by
         document name instead of version, since these documents don't carry
         distinguishing version numbers (many independently use "1.0").
      3. When the domain schema sets temporal.defaults.supersede_on_title_family,
         a version chain is inferred among same-title-family documents ordered by
         canonical valid_from (see _infer_family_supersessions). Off by default:
         dated series (meeting notes, monthly reports) share a title family
         without superseding each other — only the schema author knows which
         semantics a domain has. Explicit notices take precedence per pair.

    Additive; safe to re-run.

    When `only_doc_name` is set, the version/name maps are still built from ALL
    documents in the domain (needed to resolve the older side of the notice),
    but the notice is only parsed and applied for the named document — this lets
    commit-time per-document callers reuse the same resolution logic without
    re-scanning or re-applying notices for every other document in the domain.
    """
    report: dict = {"domain": domain, "applied": [], "dry_run": dry_run}
    with neo4j_session() as session:
        docs = session.run(
            "MATCH (d:Document) WHERE d.domain=$domain "
            "RETURN d.id AS id, d.name AS name, d.version AS version, d.valid_from AS valid_from",
            domain=domain,
        ).data()
    by_version_group: dict = {}
    for d in docs:
        if not d.get("version"):
            continue
        version = str(d["version"])
        by_version_group.setdefault(version, []).append(d)
    for version, group in by_version_group.items():
        if len(group) < 2:
            continue
        # A shared version string alone isn't a real collision — boilerplate values
        # like "1.0" are commonly reused across unrelated documents. Only warn when
        # the documents also look like the same title family (see _title_stem); a
        # cross-family collision (e.g. two unrelated policies both at "2.0") is
        # silent here and instead handled by _resolve_version_candidate below, which
        # skips rather than guesses when a citing document's notice is ambiguous.
        stems = {_title_stem(g["name"]) for g in group}
        if len(stems) > 1:
            continue
        for older, newer in zip(group, group[1:]):
            logger.warning(
                "supersession: version {!r} collision in domain {!r} between document {!r} ({}) and {!r} ({}); "
                "resolution requires a title-family match",
                version, domain, older["name"], older["id"], newer["name"], newer["id"],
            )
    by_stem_name = {Path(d["name"]).stem: d for d in docs}
    for d in docs:
        if only_doc_name is not None and d["name"] != only_doc_name:
            continue
        body = _read_doc_body(d["name"])
        if body is None:
            continue
        older = None
        effective = None
        notice = parse_supersession_notice(body)
        if notice:
            candidate = _resolve_version_candidate(d, notice["superseded_version"], by_version_group)
            if candidate:
                older, effective = candidate, notice["effective"]
        if older is None:
            table_notice = parse_supersession_metadata_table(body)
            if table_notice:
                candidate = by_stem_name.get(table_notice["superseded_doc_name"])
                if candidate and candidate["id"] != d["id"]:
                    older, effective = candidate, table_notice["effective"]
        if older is None:
            continue
        # Guarantee an effective date whenever we can: without one,
        # apply_supersession never sets older.valid_to and the old version
        # stays "current" forever (the confirmed sop_account_opening bug). Fall
        # back to the newer document's own valid_from — the date the new
        # version itself takes effect is a reasonable, safe default. If the
        # newer document also lacks one, leave effective as None rather than
        # inventing a date from nothing.
        if not effective:
            effective = d.get("valid_from")
        report["applied"].append({"newer": d["id"], "older": older["id"], "effective": effective})
        if not dry_run:
            apply_supersession(d["id"], older["id"], "document", effective, detected_by="notice")

    # ── title-family inference (schema-gated third route) ─────────────────────
    # Explicit notices above take precedence: any pair they already applied this
    # run is skipped here. detected_by distinguishes the routes in the report and
    # on the SUPERSEDES edge; notice-derived report entries keep their original
    # shape (no detected_by key).
    defaults = (load_schema(domain).get("temporal") or {}).get("defaults") or {}
    if defaults.get("supersede_on_title_family"):
        applied_pairs = {(a["newer"], a["older"]) for a in report["applied"]}
        for newer, older, effective in _infer_family_supersessions(docs, only_doc_name):
            pair = (newer["id"], older["id"])
            if pair in applied_pairs:
                continue
            applied_pairs.add(pair)
            report["applied"].append(
                {"newer": newer["id"], "older": older["id"],
                 "effective": effective, "detected_by": "title_family"}
            )
            if not dry_run:
                apply_supersession(
                    newer["id"], older["id"], "document", effective,
                    detected_by="title_family",
                )
    return report
