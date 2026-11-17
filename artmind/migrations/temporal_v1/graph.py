"""Graph steps: documents' unprefixed dates, legacy supersession records,
supersession ends, approximate record time, and the old unprefixed valid-time
indexes (spec 2026-11-15 §8; plan Task 33, questions 6 and 9).

Each step is a `pending(ctx) -> dict` (counts; empty = done) and an
`apply(ctx, dry_run) -> dict` that touches only what `pending` counts, guarded by
the same predicate, so a second run changes nothing and an interrupted run
resumes. A run with `--domain` scopes every query to `ctx.domains`
(`domain_predicate`); without one `ctx.domains` is None and every node is
covered, whatever its `_domain`. Catalogue generations are bumped before each
batched write.

- `document_dates`: a document's unprefixed `valid_from` folds into `_valid_from`
  where that is empty. An unprefixed `valid_to` is NOT folded: before this model it
  was written only by a supersession (`supersession_records._RECOMPUTE`), never a
  stated end, so it is removed and `supersession_ends` derives the end again. A
  dated document with no `_valid_precision` gets one: `none` when its
  `_valid_time_source` is its domain's default (`temporal.defaults.time_source`,
  else `default_ingestion`), else `year`/`month` by the value's shape, else `day`
  (an approximation: the parsed precision was never stored). Under `--domain` the
  newer side of every document-scope `SUPERSEDES` edge into an in-scope document is
  folded too, whatever its domain: its `_valid_from` is the end an undated edge writes.
- `supersession_records`: a document-scope edge with no `record_id` (asserted
  before supersession records) gets its record minted by core `record_for`,
  written as a curation file (declared in `writes`, so the run needs it
  git-clean), then the edge is tied to it (`record_id`, `record_fingerprint`).
- `supersession_ends`: `supersession_records.restate_validity` for every document
  with an incoming document-scope `SUPERSEDES` edge that yields an end and has a
  node (document, observations, chunks, passages, summaries) still open: they are
  closed with `_valid_to_by` markers, so `ingest unsupersede` reopens them.
- `record_time`: current nodes get `_recorded_at` = their document's `ingested_at`;
  History nodes `_retired_at` = that `ingested_at`, `_recorded_at` = the epoch and a
  `_retired_by` (below). All flagged `_record_time_approx = true`; no `ingested_at`
  → the epoch.
- `old_indexes`: drops `{entity,chunk,document}_valid_{from,to}` (core `setup` now
  creates `*_prefixed_valid_*`). Indexes belong to the whole graph, so a `--domain`
  run drops them too (idempotent: `IF EXISTS`).

`_retired_by` on History nodes (core `docs restore` revives only `retire` and
`supersede:*`, `lifecycle.REVIVABLE`): for a retired document (`DocumentHistory`),
the tag is its lifecycle record's `retired_by`, else `supersede:<record id>` of its
latest document-scope `SUPERSEDES` edge, else `retire` when a lifecycle record or
mark exists. Its observations of the document's own version take that tag; an
observation of an earlier version `n` takes `version:<n + 1>` (the commit of the
next version demoted it, as `ingest._commit_document_tx` stamps). Its chunks carry
no version: one in its latest staged `chunks.json` takes the tag, one not in it
`version:unknown`; passages and chunk summaries follow their chunk; with no staging
folder found, all take the tag (a restore brings them back, as before this model).
Everything else -- History rows of a live document that are not observations of a
known version, a retired document with no record -- gets `version:unknown`, which
nothing revives.
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from artmind.graph_query import domain_predicate

if TYPE_CHECKING:
    from artmind.migrations.temporal_v1.steps import MigrationContext

EPOCH = "1970-01-01T00:00:00+00:00"
BATCH_ROWS = 5000
#: Superseded documents restated per write transaction.
RESTATE_BATCH = 100

CURRENT_LABELS = ("Observation", "DocChunk", "DocPassage", "DocSummary", "Document")
HISTORY_LABELS = ("ObservationHistory", "DocChunkHistory", "DocPassageHistory", "DocSummaryHistory",
                  "DocumentHistory")
#: The indexes core `setup` created on the unprefixed names before this model.
OLD_INDEXES = ("entity_valid_from", "entity_valid_to", "chunk_valid_from", "chunk_valid_to",
               "document_valid_from", "document_valid_to")


def _scope(var: str, domains: list[str] | None) -> str:
    """` AND <domain predicate>`, or nothing for an unscoped run (`domains` None): then
    every node is covered, whatever its `_domain` (none, or a domain without a schema)."""
    return "" if domains is None else f" AND {domain_predicate(var)}"


def _bump(session, domains) -> None:
    """Bump the domains' catalogue generation. Called BEFORE a batched write: the batches
    commit on their own, so an interrupted run has bumped already (an extra bump is harmless)."""
    from artmind.catalogue import state as catalogue_state

    names = sorted({d for d in domains if isinstance(d, str) and d})
    if names:
        session.execute_write(lambda tx: catalogue_state.bump(tx, names))


def _count(session, cypher: str, **params) -> int:
    row = session.run(cypher, **params).single()
    try:
        return int((row or {}).get("n") or 0)
    except (TypeError, ValueError):
        return 0


def _distinct_domains(session, cypher: str, **params) -> set:
    return {r.get("domain") for r in session.run(cypher + " RETURN DISTINCT d._domain AS domain", **params).data()}


# ── document_dates ────────────────────────────────────────────────────────────

# Still to do on a document: an unprefixed name, or a date with no precision (written
# before precision existed).
_DATES_PENDING = ("(d.valid_from IS NOT NULL OR d.valid_to IS NOT NULL "
                  "OR (d._valid_precision IS NULL AND d._valid_from IS NOT NULL))")


def _doc_scope(domains: list[str] | None) -> str:
    """A document in scope, or the newer side of a document-scope SUPERSEDES edge into
    one in scope (its `_valid_from` is the end an undated edge writes), whatever its domain."""
    if domains is None:
        return ""
    return (f" AND ({domain_predicate('d')} OR EXISTS {{ MATCH (d)-[:SUPERSEDES {{scope: 'document'}}]->(o) "
            f"WHERE {domain_predicate('o')} }})")


def _dates_match(label: str, domains: list[str] | None) -> str:
    return f"MATCH (d:{label}) WHERE {_DATES_PENDING}{_doc_scope(domains)}"


# A document's precision, set once its date is folded: `none` when its date is its
# domain's default (`temporal.defaults.time_source`, as core writes it), else read
# from the stored value's shape -- `day` for a full date (an approximation: an old
# document's parsed precision was never stored).
_FOLD = """
CALL (d) {
  SET d._valid_from = coalesce(d._valid_from, d.valid_from)
  REMOVE d.valid_from, d.valid_to
  WITH d WHERE d._valid_precision IS NULL AND d._valid_from IS NOT NULL
  SET d._valid_precision = CASE
    WHEN d._valid_time_source = coalesce($sources[coalesce(d._domain, '')], 'default_ingestion') THEN 'none'
    WHEN size(toString(d._valid_from)) = 4 THEN 'year'
    WHEN size(toString(d._valid_from)) = 7 THEN 'month'
    ELSE 'day' END
} IN TRANSACTIONS OF %d ROWS""" % BATCH_ROWS


def default_sources(domains) -> dict[str, str]:
    """Each domain's default `_valid_time_source` (`temporal.defaults.time_source`, from its
    schema or its dotted parent's; `default_ingestion` when neither declares one)."""
    from artmind import temporal

    out: dict[str, str] = {}
    for domain in sorted({d for d in domains if isinstance(d, str) and d}):
        schema = temporal.load_schema(domain)
        if not schema and "." in domain:
            schema = temporal.load_schema(domain.split(".", 1)[0])
        source = ((schema.get("temporal") or {}).get("defaults") or {}).get("time_source")
        out[domain] = source if isinstance(source, str) and source else "default_ingestion"
    return out


def document_dates_pending(ctx: MigrationContext) -> dict:
    with ctx.session_factory() as session:
        counts = {label: _count(session, _dates_match(label, ctx.domains) + " RETURN count(d) AS n",
                                domains=ctx.domains)
                  for label in ("Document", "DocumentHistory")}
    return {k: v for k, v in counts.items() if v}


def document_dates_apply(ctx: MigrationContext, dry_run: bool = False) -> dict:
    if dry_run:
        return {}
    applied, touched = {}, {}
    with ctx.session_factory() as session:
        for label in ("Document", "DocumentHistory"):
            touched[label] = _distinct_domains(session, _dates_match(label, ctx.domains), domains=ctx.domains)
            applied[label] = _count(session, _dates_match(label, ctx.domains) + " RETURN count(d) AS n",
                                    domains=ctx.domains)
        everywhere = set().union(*touched.values())
        _bump(session, everywhere)
        sources = default_sources(everywhere)
        for label in ("Document", "DocumentHistory"):
            if not applied[label]:
                continue
            # `valid_to` is a supersession's end, never a stated one: removed, not folded
            # (`supersession_ends` derives it again with a `_valid_to_by` marker).
            session.run(_dates_match(label, ctx.domains) + _FOLD, domains=ctx.domains, sources=sources).consume()
    return {k: v for k, v in applied.items() if v}


# ── supersession_records ──────────────────────────────────────────────────────

# Document-scope edges asserted before supersession records existed: no `record_id`, so
# `restate_validity` skips them until a record is minted.
_LEGACY_EDGES = ("MATCH (newer)-[s:SUPERSEDES {{scope: 'document'}}]->(older) "
                 "WHERE s.record_id IS NULL AND (older:Document OR older:DocumentHistory){scope} ")

_TIE_EDGE = """
OPTIONAL MATCH (n1:Document {id: $newer}) OPTIONAL MATCH (n2:DocumentHistory {id: $newer})
WITH coalesce(n1, n2) AS newer
OPTIONAL MATCH (o1:Document {id: $older}) OPTIONAL MATCH (o2:DocumentHistory {id: $older})
WITH newer, coalesce(o1, o2) AS older
MATCH (newer)-[s:SUPERSEDES {scope: 'document'}]->(older)
WHERE s.record_id IS NULL
SET s.record_id = $id, s.record_fingerprint = $fingerprint, s.effective = coalesce(s.effective, $effective),
    s.detected_by = coalesce(s.detected_by, $detected_by)
RETURN count(s) AS n
"""


def _legacy_edges(session, domains) -> list[dict]:
    rows = session.run(_LEGACY_EDGES.format(scope=_scope("older", domains))
                       + "RETURN newer.id AS newer, older.id AS older, s.effective AS effective, "
                         "s.detected_by AS detected_by ORDER BY newer, older", domains=domains).data()
    return [r for r in rows if isinstance(r, dict) and r.get("newer") and r.get("older")]


def supersession_records_pending(ctx: MigrationContext) -> dict:
    with ctx.session_factory() as session:
        n = _count(session, _LEGACY_EDGES.format(scope=_scope("older", ctx.domains)) + "RETURN count(s) AS n",
                   domains=ctx.domains)
    return {"edges": n} if n else {}


def supersession_records_writes(ctx: MigrationContext) -> list:
    """The record files the step will write (the run requires them git-clean)."""
    from artmind import curation_records, supersession_records

    try:
        with ctx.session_factory() as session:
            edges = _legacy_edges(session, ctx.domains)
    except Exception:  # noqa: BLE001 - an unreachable graph is its own blocker
        return []
    return [curation_records.record_path(supersession_records.NAME,
                                         supersession_records.record_id(e["newer"], e["older"], "document"))
            for e in edges]


def supersession_records_apply(ctx: MigrationContext, dry_run: bool = False) -> dict:
    """Mint each legacy edge's record with core `record_for`, write its file, then tie the
    edge to it (`record_id`, `record_fingerprint`). The file is written first, so an
    interrupted run finds the edge still untied and rewrites the same bytes."""
    from artmind import curation_records, supersession_records

    if dry_run:
        return {}
    written = 0
    with ctx.session_factory() as session:
        for edge in _legacy_edges(session, ctx.domains):
            record = supersession_records.record_for(
                session, edge["newer"], edge["older"], "document", edge.get("effective"),
                edge.get("detected_by") or "migration",
            )
            fingerprint = curation_records.write_record(supersession_records.NAME, record)
            session.execute_write(lambda tx, r=record, f=fingerprint: tx.run(
                _TIE_EDGE, newer=r["newer_doc_id"], older=r["older_doc_id"], id=r["id"], fingerprint=f,
                effective=r.get("effective"), detected_by=r.get("detected_by")).consume())
            written += 1
    return {"records": written} if written else {}


# ── supersession_ends ─────────────────────────────────────────────────────────

def _open_node() -> str:
    """Any node of `older` (other than itself) still without an end."""
    from artmind.supersession_records import VALIDITY_TARGETS

    parts = ["older._valid_to IS NULL"]
    for label, key in VALIDITY_TARGETS:
        if key == "doc_id":
            parts.append(f"EXISTS {{ MATCH (x:{label} {{doc_id: older.id}}) WHERE x._valid_to IS NULL }}")
    return "(" + " OR ".join(parts) + ")"


def _superseded_open(domains: list[str] | None) -> str:
    # The end a document-scope edge writes: as `supersession_records._DOCUMENT_EDGES`, which
    # skips an edge with no `record_id` (`supersession_records` step mints them first). Only
    # prefixed dates count: `document_dates` folds the newer side of every edge first.
    return (
        "MATCH (newer)-[s:SUPERSEDES {scope: 'document'}]->(older) "
        f"WHERE (older:Document OR older:DocumentHistory){_scope('older', domains)} "
        "AND s.record_id IS NOT NULL "
        "AND coalesce(s.effective, CASE WHEN newer._valid_precision = 'none' THEN null "
        "ELSE newer._valid_from END) IS NOT NULL "
        f"WITH DISTINCT older WHERE {_open_node()} "
    )


def supersession_ends_pending(ctx: MigrationContext) -> dict:
    with ctx.session_factory() as session:
        n = _count(session, _superseded_open(ctx.domains) + "RETURN count(older) AS n", domains=ctx.domains)
    return {"documents": n} if n else {}


def _restate_batch(tx, ids: list[str]) -> int:
    """Restate each superseded document's ends, rebuild the keys of the live ones'
    observations (an entity takes `_valid_to` from its winner) and bump their domains."""
    from artmind import projection, supersession_records
    from artmind.catalogue import state as catalogue_state

    closed, keys = 0, set()
    for older in ids:
        closed += supersession_records.restate_validity(tx, older)
        keys |= set(projection.keys_for_document(tx, older, status="latest"))
        catalogue_state.bump_for_document(tx, older)
    if keys:
        projection.rebuild(tx, keys, synthesis_loader=lambda ks: projection.load_synthesis_batch(tx, ks))
    return closed


def supersession_ends_apply(ctx: MigrationContext, dry_run: bool = False) -> dict:
    if dry_run:
        return {}
    with ctx.session_factory() as session:
        ids = sorted({r["id"] for r in session.run(_superseded_open(ctx.domains) + "RETURN older.id AS id",
                                                   domains=ctx.domains).data() if r.get("id")})
        closed = 0
        for i in range(0, len(ids), RESTATE_BATCH):
            batch = ids[i:i + RESTATE_BATCH]
            closed += session.execute_write(lambda tx, b=batch: _restate_batch(tx, b))
    return {"documents": len(ids), "nodes_closed": closed} if ids else {}


# ── record_time ───────────────────────────────────────────────────────────────

def _join_doc(var: str, label: str) -> str:
    """`d`: the document node a row belongs to (the row itself for a document)."""
    if label in ("Document", "DocumentHistory"):
        return f"WITH {var}, {var} AS d "
    return (
        f"OPTIONAL MATCH (d1:Document {{id: {var}.doc_id}}) "
        f"OPTIONAL MATCH (d2:DocumentHistory {{id: {var}.doc_id}}) "
        f"OPTIONAL MATCH (d3:UserChat {{id: {var}.doc_id}}) "
        f"OPTIONAL MATCH (d4:UserChatHistory {{id: {var}.doc_id}}) "
        f"WITH {var}, coalesce(d1, d2, d3, d4) AS d "
    )


def _needs(label: str) -> str:
    if label in CURRENT_LABELS:
        return "x._recorded_at IS NULL"
    return "(x._retired_at IS NULL OR x._retired_by IS NULL)"


def _match(label: str, domains: list[str] | None) -> str:
    return f"MATCH (x:{label}) WHERE {_needs(label)}{_scope('x', domains)} "


_TAG = "$tags[coalesce(x.doc_id, x.id)]"
#: The chunk a History row belongs to (a document-level summary has none).
_CHUNK_OF = {"DocChunkHistory": "x.id", "DocPassageHistory": "x.chunk_id", "DocSummaryHistory": "x.chunk_id"}


def _retired_by(label: str) -> str:
    """The `_retired_by` for a History row `x` of document `d`. `$tags` maps a retired
    document's id to what retired it; `$chunks` maps it to the chunk ids of its latest
    staged version (`chunks.json`), when that folder was found."""
    if label == "ObservationHistory":
        return (
            "CASE WHEN x.doc_version IS NULL THEN 'version:unknown' "
            f"WHEN {_TAG} IS NOT NULL AND (d.version IS NULL OR toInteger(x.doc_version) >= toInteger(d.version)) "
            f"THEN {_TAG} "
            "WHEN d.version IS NOT NULL AND toInteger(x.doc_version) < toInteger(d.version) "
            "THEN 'version:' + toString(toInteger(x.doc_version) + 1) "
            "ELSE 'version:unknown' END"
        )
    if label in _CHUNK_OF:
        chunk, staged = _CHUNK_OF[label], "$chunks[coalesce(x.doc_id, x.id)]"
        return (
            f"CASE WHEN {_TAG} IS NULL THEN 'version:unknown' "
            f"WHEN {staged} IS NULL OR {chunk} IS NULL OR {chunk} IN {staged} THEN {_TAG} "
            "ELSE 'version:unknown' END"
        )
    return f"coalesce({_TAG}, 'version:unknown')"


def _set(label: str) -> str:
    if label in CURRENT_LABELS:
        return "SET x._recorded_at = coalesce(d.ingested_at, $epoch), x._record_time_approx = true"
    return (
        "SET x._retired_at = coalesce(x._retired_at, d.ingested_at, $epoch), "
        "x._recorded_at = coalesce(x._recorded_at, $epoch), "
        f"x._retired_by = coalesce(x._retired_by, {_retired_by(label)}), "
        "x._record_time_approx = true"
    )


def record_time_pending(ctx: MigrationContext) -> dict:
    out: dict = {}
    with ctx.session_factory() as session:
        for label in (*CURRENT_LABELS, *HISTORY_LABELS):
            n = _count(session, _match(label, ctx.domains) + "RETURN count(x) AS n", domains=ctx.domains)
            if not n:
                continue
            out[label] = n
            # Rows whose document has no `ingested_at` (counted only while pending).
            unknown = _count(session, _match(label, ctx.domains) + _join_doc("x", label)
                             + "WHERE d.ingested_at IS NULL RETURN count(x) AS n", domains=ctx.domains)
            if unknown:
                out.setdefault("unknown", {})[label] = unknown
    return out


_RETIRED_DOCS = """
MATCH (d:DocumentHistory) WHERE true{scope}
OPTIONAL MATCH (n)-[s:SUPERSEDES {{scope: 'document'}}]->(d)
WITH d, n, s ORDER BY coalesce(s.effective, '') DESC, n.id DESC
RETURN d.id AS id, d._domain AS domain, d.lifecycle_record AS lifecycle_record, collect(s.record_id) AS records
"""


def _tag(row: dict) -> str | None:
    """What retired one `DocumentHistory` document, or None when no record says."""
    from artmind import curation_records, lifecycle_records

    doc_id = row.get("id")
    if not isinstance(doc_id, str) or not doc_id:
        return None
    record = curation_records.read_record(lifecycle_records.NAME, lifecycle_records.record_id(doc_id))
    by = (record or {}).get("retired_by")
    if isinstance(by, str) and (by == "retire" or by.startswith("supersede:")):
        return by
    records = [r for r in row.get("records") or [] if isinstance(r, str) and r]
    if records:
        return f"supersede:{records[0]}"
    if record is not None or row.get("lifecycle_record"):
        return "retire"
    return None


def retirement_tags(session, domains: list[str] | None) -> tuple[dict[str, str], dict[str, str]]:
    """`({doc id: tag}, {doc id: domain})` for the retired documents in scope."""
    rows = [r for r in session.run(_RETIRED_DOCS.format(scope=_scope("d", domains)), domains=domains).data()
            if isinstance(r, dict)]
    tags = {row["id"]: tag for row in rows if (tag := _tag(row))}
    return tags, {row["id"]: row.get("domain") for row in rows if row.get("id") in tags}


def staged_chunk_ids(doc_domains: dict[str, str]) -> dict[str, list[str]]:
    """The chunk ids of each document's staged version (`kg/<domain>/*/chunks.json`), for
    the documents whose staging folder is found (one scan per domain, matched by id)."""
    import json

    import paths

    by_domain: dict[str, set] = {}
    for doc_id, domain in doc_domains.items():
        if isinstance(domain, str) and domain:
            by_domain.setdefault(domain, set()).add(doc_id)
    out: dict[str, list[str]] = {}
    for domain, wanted in by_domain.items():
        folder = Path(paths.KG_DIR) / domain
        if not folder.is_dir():
            continue
        for document_json in sorted(folder.glob("*/document.json")):
            if document_json.parent.name.endswith((".artmind-tmp", ".artmind-old")):
                continue
            try:
                doc_id = json.loads(document_json.read_text(encoding="utf-8")).get("id")
                if doc_id not in wanted or doc_id in out:
                    continue
                chunks = json.loads((document_json.parent / "chunks.json").read_text(encoding="utf-8"))
            except (OSError, ValueError, AttributeError):
                continue
            if isinstance(chunks, list):
                out[doc_id] = [c["id"] for c in chunks if isinstance(c, dict) and isinstance(c.get("id"), str)]
    return out


def record_time_apply(ctx: MigrationContext, dry_run: bool = False) -> dict:
    if dry_run:
        return {}
    applied: dict = {}
    with ctx.session_factory() as session:
        tags, doc_domains = retirement_tags(session, ctx.domains)
        chunks = staged_chunk_ids(doc_domains)
        domains: set = set()
        for label in (*CURRENT_LABELS, *HISTORY_LABELS):
            n = _count(session, _match(label, ctx.domains) + "RETURN count(x) AS n", domains=ctx.domains)
            if n:
                applied[label] = n
                domains |= {r.get("domain") for r in session.run(
                    _match(label, ctx.domains) + "RETURN DISTINCT x._domain AS domain", domains=ctx.domains).data()}
        _bump(session, domains)
        for label in applied:
            session.run(
                _match(label, ctx.domains) + _join_doc("x", label)
                + "CALL (x, d) { " + _set(label) + " } IN TRANSACTIONS OF %d ROWS" % BATCH_ROWS,
                domains=ctx.domains, epoch=EPOCH, tags=tags, chunks=chunks,
            ).consume()
    return applied


# ── old_indexes ───────────────────────────────────────────────────────────────

_SHOW_OLD = (
    "SHOW INDEXES YIELD name, properties "
    "WHERE name IN $names AND any(p IN properties WHERE p IN ['valid_from', 'valid_to']) "
    "RETURN name"
)


def _old_indexes(session) -> list[str]:
    return sorted(r["name"] for r in session.run(_SHOW_OLD, names=list(OLD_INDEXES)).data()
                  if r.get("name") in OLD_INDEXES)


def old_indexes_pending(ctx: MigrationContext) -> dict:
    with ctx.session_factory() as session:
        n = len(_old_indexes(session))
    return {"indexes": n} if n else {}


def old_indexes_apply(ctx: MigrationContext, dry_run: bool = False) -> dict:
    if dry_run:
        return {}
    with ctx.session_factory() as session:
        names = _old_indexes(session)
        for name in names:  # a fixed name from OLD_INDEXES, never input
            session.run(f"DROP INDEX {name} IF EXISTS").consume()
    return {"indexes": len(names)} if names else {}
