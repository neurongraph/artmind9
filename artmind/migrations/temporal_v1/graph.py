"""Graph steps: documents' unprefixed dates, supersession ends, approximate record
time, and the old unprefixed valid-time indexes (spec 2026-11-15 §8; plan Task 33,
questions 6 and 9).

Each step is a `pending(ctx) -> dict` (counts; empty = done) and an
`apply(ctx, dry_run) -> dict` that touches only what `pending` counts, guarded by
the same predicate, so a second run changes nothing and an interrupted run
resumes. Every query is scoped to `ctx.domains` (`domain_predicate`), except the
index step: indexes belong to the whole graph.

- `document_dates`: a Document's unprefixed `valid_from` folds into `_valid_from`
  where that is empty. An unprefixed `valid_to` is NOT folded: before this model it
  was written only by a supersession (`supersession_records._RECOMPUTE`), never a
  stated end, so it is removed and `supersession_ends` derives the end again.
- `supersession_ends`: `supersession_records.restate_validity` for every document
  with an incoming document-scope `SUPERSEDES` edge that yields an end and has a
  node (document, observations, chunks, passages, summaries) still open: they are
  closed with `_valid_to_by` markers, so `ingest unsupersede` reopens them.
- `record_time`: current nodes get `_recorded_at` = their document's `ingested_at`;
  History nodes `_retired_at` = that `ingested_at`, `_recorded_at` = the epoch and a
  `_retired_by` (below). All flagged `_record_time_approx = true`; no `ingested_at`
  → the epoch.
- `old_indexes`: drops `{entity,chunk,document}_valid_{from,to}` (core `setup` now
  creates `*_prefixed_valid_*`).

`_retired_by` on History nodes (core `docs restore` revives only `retire` and
`supersede:*`, `lifecycle.REVIVABLE`): for a retired document (`DocumentHistory`),
the tag is its lifecycle record's `retired_by`, else `supersede:<record id>` of its
latest document-scope `SUPERSEDES` edge, else `retire` when a lifecycle record or
mark exists. Its observations of the document's own version take that tag; an
observation of an earlier version `n` takes `version:<n + 1>` (the commit of the
next version demoted it, as `ingest._commit_document_tx` stamps). Its chunks,
passages and summaries carry no version, so they take the tag (a restore brings
them back, as before this model). Everything else -- History rows of a live
document that are not observations of a known version, a retired document with no
record -- gets `version:unknown`, which nothing revives.
"""
from __future__ import annotations

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


def _scope(var: str) -> str:
    return f" AND {domain_predicate(var)}"


def _bump(session, domains) -> None:
    from artmind.catalogue import state as catalogue_state

    names = [d for d in domains if d]
    if names:
        session.execute_write(lambda tx: catalogue_state.bump(tx, names))


def _count(session, cypher: str, **params) -> int:
    row = session.run(cypher, **params).single()
    try:
        return int((row or {}).get("n") or 0)
    except (TypeError, ValueError):
        return 0


# ── document_dates ────────────────────────────────────────────────────────────

_UNPREFIXED = "(d.valid_from IS NOT NULL OR d.valid_to IS NOT NULL)"


def _dates_match(label: str) -> str:
    return f"MATCH (d:{label}) WHERE {_UNPREFIXED}{_scope('d')}"


def document_dates_pending(ctx: MigrationContext) -> dict:
    with ctx.session_factory() as session:
        counts = {label: _count(session, _dates_match(label) + " RETURN count(d) AS n", domains=ctx.domains)
                  for label in ("Document", "DocumentHistory")}
    return {k: v for k, v in counts.items() if v}


def document_dates_apply(ctx: MigrationContext, dry_run: bool = False) -> dict:
    if dry_run:
        return {}
    applied = {}
    with ctx.session_factory() as session:
        domains = set()
        for label in ("Document", "DocumentHistory"):
            domains |= {r.get("domain") for r in session.run(
                _dates_match(label) + " RETURN DISTINCT d._domain AS domain", domains=ctx.domains).data()}
            applied[label] = _count(session, _dates_match(label) + " RETURN count(d) AS n", domains=ctx.domains)
            # `valid_to` is a supersession's end, never a stated one: removed, not folded
            # (`supersession_ends` derives it again with a `_valid_to_by` marker).
            session.run(
                _dates_match(label) + """
                CALL (d) {
                  SET d._valid_from = coalesce(d._valid_from, d.valid_from)
                  REMOVE d.valid_from, d.valid_to
                } IN TRANSACTIONS OF %d ROWS""" % BATCH_ROWS,
                domains=ctx.domains,
            ).consume()
        _bump(session, sorted(d for d in domains if isinstance(d, str)))
    return {k: v for k, v in applied.items() if v}


# ── supersession_ends ─────────────────────────────────────────────────────────

def _open_node() -> str:
    """Any node of `older` (other than itself) still without an end."""
    from artmind.supersession_records import VALIDITY_TARGETS

    parts = ["older._valid_to IS NULL"]
    for label, key in VALIDITY_TARGETS:
        if key == "doc_id":
            parts.append(f"EXISTS {{ MATCH (x:{label} {{doc_id: older.id}}) WHERE x._valid_to IS NULL }}")
    return "(" + " OR ".join(parts) + ")"


def _superseded_open() -> str:
    # The end a document-scope edge writes: as `supersession_records._DOCUMENT_EDGES`, which
    # skips an edge with no `record_id`. The newer document's unprefixed `valid_from` counts
    # too, so the count is right before `document_dates` has folded it (it runs first).
    return (
        "MATCH (newer)-[s:SUPERSEDES {scope: 'document'}]->(older) "
        f"WHERE (older:Document OR older:DocumentHistory){_scope('older')} "
        "AND s.record_id IS NOT NULL "
        "AND coalesce(s.effective, CASE WHEN newer._valid_precision = 'none' THEN null "
        "ELSE coalesce(newer._valid_from, newer.valid_from) END) IS NOT NULL "
        f"WITH DISTINCT older WHERE {_open_node()} "
    )


def supersession_ends_pending(ctx: MigrationContext) -> dict:
    with ctx.session_factory() as session:
        n = _count(session, _superseded_open() + "RETURN count(older) AS n", domains=ctx.domains)
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
        ids = sorted({r["id"] for r in session.run(_superseded_open() + "RETURN older.id AS id",
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


def _match(label: str) -> str:
    return f"MATCH (x:{label}) WHERE {_needs(label)}{_scope('x')} "


def _retired_by(label: str) -> str:
    """The `_retired_by` for a History row `x` of document `d`; `$tags` maps a retired
    document's id to what retired it."""
    tag = "$tags[coalesce(x.doc_id, x.id)]"
    if label == "ObservationHistory":
        return (
            "CASE WHEN x.doc_version IS NULL THEN 'version:unknown' "
            f"WHEN {tag} IS NOT NULL AND (d.version IS NULL OR toInteger(x.doc_version) >= toInteger(d.version)) "
            f"THEN {tag} "
            "WHEN d.version IS NOT NULL AND toInteger(x.doc_version) < toInteger(d.version) "
            "THEN 'version:' + toString(toInteger(x.doc_version) + 1) "
            "ELSE 'version:unknown' END"
        )
    return f"coalesce({tag}, 'version:unknown')"


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
            n = _count(session, _match(label) + "RETURN count(x) AS n", domains=ctx.domains)
            if not n:
                continue
            out[label] = n
            # Rows whose document has no `ingested_at` (counted only while pending).
            unknown = _count(session, _match(label) + _join_doc("x", label)
                             + "WHERE d.ingested_at IS NULL RETURN count(x) AS n", domains=ctx.domains)
            if unknown:
                out.setdefault("unknown", {})[label] = unknown
    return out


_RETIRED_DOCS = """
MATCH (d:DocumentHistory) WHERE true{scope}
OPTIONAL MATCH (n)-[s:SUPERSEDES {{scope: 'document'}}]->(d)
WITH d, n, s ORDER BY coalesce(s.effective, '') DESC, n.id DESC
RETURN d.id AS id, d.lifecycle_record AS lifecycle_record, collect(s.record_id) AS records
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


def retirement_tags(session, domains: list[str]) -> dict[str, str]:
    rows = session.run(_RETIRED_DOCS.format(scope=_scope("d")), domains=domains).data()
    return {row["id"]: tag for row in rows if isinstance(row, dict) and (tag := _tag(row))}


def record_time_apply(ctx: MigrationContext, dry_run: bool = False) -> dict:
    if dry_run:
        return {}
    applied: dict = {}
    with ctx.session_factory() as session:
        tags = retirement_tags(session, ctx.domains)
        domains = set()
        for label in (*CURRENT_LABELS, *HISTORY_LABELS):
            n = _count(session, _match(label) + "RETURN count(x) AS n", domains=ctx.domains)
            if not n:
                continue
            applied[label] = n
            domains |= {r.get("domain") for r in session.run(
                _match(label) + "RETURN DISTINCT x._domain AS domain", domains=ctx.domains).data()}
            session.run(
                _match(label) + _join_doc("x", label)
                + "CALL (x, d) { " + _set(label) + " } IN TRANSACTIONS OF %d ROWS" % BATCH_ROWS,
                domains=ctx.domains, epoch=EPOCH, tags=tags,
            ).consume()
        _bump(session, sorted(d for d in domains if isinstance(d, str)))
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
