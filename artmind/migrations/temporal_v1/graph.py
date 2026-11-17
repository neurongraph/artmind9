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

Between a killed run's `document_dates` (which removes the unprefixed `valid_to`, the
old supersession end) and its `supersession_ends`, superseded documents read as open
until the run resumes. Graph changes are not reversible by git; the steps do not
travel (each machine migrates its own graph).

- `document_dates`: a document's unprefixed `valid_from` folds into `_valid_from`
  where that is empty. An unprefixed `valid_to` is NOT folded: before this model it
  was written only by a supersession (`supersession_records._RECOMPUTE`), never a
  stated end, so it is removed and `supersession_ends` derives the end again. A
  dated document with no `_valid_precision` gets one: `none` when its
  `_valid_time_source` is its domain's default (`temporal.defaults.time_source`,
  else `default_ingestion`), else `day` for a full date (an approximation: the parsed
  precision was never stored); a partial date is left to `partial_dates`. Under `--domain` the
  newer side of every document-scope `SUPERSEDES` edge into an in-scope document is
  folded too, whatever its domain: its `_valid_from` is the end an undated edge writes.
- `partial_dates`: a partial date string (`YYYY`, `YYYY-MM`; old `parse_iso` kept them)
  in `_valid_from`/`_valid_to`/`_doc_valid_from`/`_doc_valid_to` on documents, their
  rows and entities is normalised with core `parse_valid`: a start to its first day
  (`_valid_precision` year/month when unset), an end to the period's exclusive end. A
  legacy `SUPERSEDES.effective` that is partial or not a string (an integer year, a
  Cypher date) becomes the string of its start (a supersession end is the newer
  document's start). Values are read as strings (`toString`); one `parse_valid` cannot
  read is never pending: it is counted under `unparseable`, which does not block done.
  Runs after `document_dates` (which then sets only `none` or `day` precision).
- `supersession_records`: a document-scope edge with no `record_id` (asserted
  before supersession records) gets its record minted by core `record_for`,
  written as a curation file (declared in `writes`, so the run needs it
  git-clean), then the edge is tied to it (`record_id`, `record_fingerprint`). A file
  a killed run wrote before tying its edge holds the same bytes this step would write
  (`own_work`), so it does not block the run that finishes the job.
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
#: A partial date string (`YYYY`, `YYYY-MM`) as old graphs hold them (before `parse_valid`,
#: `parse_iso` returned them unchanged); `partial_dates` normalises them. Cypher's `=~`
#: matches the whole string.
PARTIAL = "[0-9]{4}(-(0[1-9]|1[0-2]))?"
#: A date `partial_dates` can make a string of (a partial date, or a full one).
DATE_SHAPE = "[0-9]{4}(-(0[1-9]|1[0-2])(-[0-9]{2})?)?"

_DATES_PENDING = ("(d.valid_from IS NOT NULL OR d.valid_to IS NOT NULL "
                  "OR (d._valid_precision IS NULL AND d._valid_from IS NOT NULL "
                  f"AND NOT toString(d._valid_from) =~ '{PARTIAL}'))")


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
# domain's default (`temporal.defaults.time_source`, as core writes it), else `day` for
# a full date (an approximation: an old document's parsed precision was never stored).
# A partial date is left to `partial_dates`, which reads its precision with `parse_valid`.
_FOLD = """
CALL (d) {
  SET d._valid_from = coalesce(d._valid_from, d.valid_from)
  REMOVE d.valid_from, d.valid_to
  WITH d WHERE d._valid_precision IS NULL AND d._valid_from IS NOT NULL
    AND NOT toString(d._valid_from) =~ '%s'
  SET d._valid_precision = CASE
    WHEN d._valid_time_source = coalesce($sources[coalesce(d._domain, '')], 'default_ingestion') THEN 'none'
    ELSE 'day' END
} IN TRANSACTIONS OF %d ROWS""" % (PARTIAL, BATCH_ROWS)


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


# ── partial_dates ─────────────────────────────────────────────────────────────

PARTIAL_LABELS = ("Document", "DocumentHistory", "Observation", "ObservationHistory", "DocChunk",
                  "DocChunkHistory", "DocPassage", "DocPassageHistory", "DocSummary", "DocSummaryHistory",
                  "Entity")
_DATE_KEYS = ("_valid_from", "_valid_to", "_doc_valid_from", "_doc_valid_to")


def _partial(var: str, key: str) -> str:
    # `toString`: an old YAML date can be an integer (2020); a Cypher date or datetime
    # renders as a full date and does not match.
    return f"toString({var}.{key}) =~ '{PARTIAL}'"


def _any_partial(var: str = "x") -> str:
    return "(" + " OR ".join(_partial(var, k) for k in _DATE_KEYS) + ")"


def _readable(var: str = "x") -> str:
    """A row holding a partial value `parse_valid` reads (`$readable`)."""
    return "(" + " OR ".join(f"toString({var}.{k}) IN $readable" for k in _DATE_KEYS) + ")"


def _partial_match(label: str, domains: list[str] | None, where: str | None = None) -> str:
    return f"MATCH (x:{label}) WHERE {where or _any_partial()}{_scope('x', domains)} "


# A legacy `SUPERSEDES.effective`: a partial date, or not a string at all (an integer
# year, a Cypher date). Core reads only a string end (`restate_validity`).
_EDGE_EFFECTIVE = (f"s.effective IS NOT NULL AND (NOT s.effective IS :: STRING "
                   f"OR toString(s.effective) =~ '{PARTIAL}')")


def _edge_match(domains: list[str] | None, where: str | None = None) -> str:
    return ("MATCH ()-[s:SUPERSEDES]->(older) "
            f"WHERE {where or _EDGE_EFFECTIVE}{_scope('older', domains)} ")


def partial_map(values) -> dict[str, dict]:
    """Core `parse_valid` over each partial value (as a string): its first day (`start`),
    the exclusive end of its period (`end`; None when open) and its precision. Values it
    cannot read are left out."""
    from artmind import temporal

    out: dict[str, dict] = {}
    for value in sorted({str(v) for v in values if v is not None}):
        parsed = temporal.parse_valid(value)
        if parsed is not None:
            out[value] = {"start": parsed.start, "end": parsed.implied_end, "precision": parsed.precision}
    return out


def _normalise() -> str:
    """Pad a partial start to its first day, turn a partial end into the period's exclusive
    end (None = open: removed), and keep the precision a partial `_valid_from` stated."""
    lookups = ", ".join(f"CASE WHEN {_partial('x', k)} THEN $map[toString(x.{k})] END AS {k.strip('_')}"
                        for k in _DATE_KEYS)
    return (
        f"CALL (x) {{ WITH x, {lookups} "
        "SET x._valid_precision = CASE WHEN valid_from IS NULL THEN x._valid_precision "
        "ELSE coalesce(x._valid_precision, valid_from.precision) END, "
        "x._valid_from = CASE WHEN valid_from IS NULL THEN x._valid_from ELSE valid_from.start END, "
        "x._doc_valid_from = CASE WHEN doc_valid_from IS NULL THEN x._doc_valid_from ELSE doc_valid_from.start END, "
        "x._valid_to = CASE WHEN valid_to IS NULL THEN x._valid_to ELSE valid_to.end END, "
        "x._doc_valid_to = CASE WHEN doc_valid_to IS NULL THEN x._doc_valid_to ELSE doc_valid_to.end END "
        f"}} IN TRANSACTIONS OF {BATCH_ROWS} ROWS"
    )


# A supersession's end is the newer document's start: a partial `effective` takes its first day.
_NORMALISE_EDGE = ("CALL (s) { WITH s, $map[toString(s.effective)] AS e "
                   f"SET s.effective = e.start }} IN TRANSACTIONS OF {BATCH_ROWS} ROWS")


def _values(session, label: str | None, domains) -> set[str]:
    """The distinct partial values (as strings) still on `label`'s rows, or on edges."""
    if label is None:
        cypher = _edge_match(domains) + "RETURN collect(DISTINCT toString(s.effective)) AS values"
    else:
        cypher = (_partial_match(label, domains)
                  + "UNWIND [" + ", ".join(f"toString(x.{k})" for k in _DATE_KEYS) + "] AS v "
                  f"WITH v WHERE v =~ '{PARTIAL}' RETURN collect(DISTINCT v) AS values")
    rows = session.run(cypher, domains=domains).data()
    return {str(v) for v in ((rows[0] if rows else {}).get("values") or []) if v is not None}


def _partial_plan(session, domains) -> dict:
    """Per label (`SUPERSEDES` for edges): `(readable values, rows to do, unparseable rows)`.
    A row whose partial values `parse_valid` cannot read is never pending: it is reported
    under `unparseable` and left as it is."""
    plan = {}
    for label in (*PARTIAL_LABELS, None):
        values = _values(session, label, domains)
        if not values:
            continue
        readable = sorted(partial_map(values))
        unreadable = sorted(values - set(readable))
        name = label or "SUPERSEDES"
        if label is None:
            todo = _count(session, _edge_match(domains, f"{_EDGE_EFFECTIVE} AND toString(s.effective) IN $readable")
                          + "RETURN count(s) AS n", domains=domains, readable=readable) if readable else 0
            bad = _count(session, _edge_match(domains, f"{_EDGE_EFFECTIVE} AND NOT toString(s.effective) IN $readable")
                         + "RETURN count(s) AS n", domains=domains, readable=readable) if unreadable else 0
        else:
            todo = _count(session, _partial_match(label, domains, _readable()) + "RETURN count(x) AS n",
                          domains=domains, readable=readable) if readable else 0
            bad = _count(session, _partial_match(label, domains, f"{_any_partial()} AND NOT {_readable()}")
                         + "RETURN count(x) AS n", domains=domains, readable=readable) if unreadable else 0
        plan[name] = (readable, todo, bad)
    return plan


def partial_dates_pending(ctx: MigrationContext) -> dict:
    with ctx.session_factory() as session:
        plan = _partial_plan(session, ctx.domains)
    out: dict = {name: todo for name, (_, todo, _) in plan.items() if todo}
    unparseable = {name: bad for name, (_, _, bad) in plan.items() if bad}
    if unparseable:
        out["unparseable"] = unparseable  # informs; never blocks done (steps.INFO_KEYS)
    return out


def partial_dates_apply(ctx: MigrationContext, dry_run: bool = False) -> dict:
    if dry_run:
        return {}
    with ctx.session_factory() as session:
        plan = {name: p for name, p in _partial_plan(session, ctx.domains).items() if p[1]}
        if not plan:
            return {}
        readable = sorted({v for values, _, _ in plan.values() for v in values})
        mapping = partial_map(readable)
        domains: set = set()
        for name in plan:
            if name == "SUPERSEDES":
                domains |= {r.get("domain") for r in session.run(
                    _edge_match(ctx.domains, f"{_EDGE_EFFECTIVE} AND toString(s.effective) IN $readable")
                    + "RETURN DISTINCT older._domain AS domain", domains=ctx.domains, readable=readable).data()}
            else:
                domains |= {r.get("domain") for r in session.run(
                    _partial_match(name, ctx.domains, _readable()) + "RETURN DISTINCT x._domain AS domain",
                    domains=ctx.domains, readable=readable).data()}
        _bump(session, domains)
        for name in plan:
            if name == "SUPERSEDES":
                session.run(_edge_match(ctx.domains, f"{_EDGE_EFFECTIVE} AND toString(s.effective) IN $readable")
                            + _NORMALISE_EDGE, domains=ctx.domains, readable=readable, map=mapping).consume()
            else:
                session.run(_partial_match(name, ctx.domains, _readable()) + _normalise(),
                            domains=ctx.domains, readable=readable, map=mapping).consume()
    return {name: todo for name, (_, todo, _) in plan.items()}


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


def _mint(session, edge: dict) -> dict:
    from artmind import supersession_records

    effective = edge.get("effective")
    return supersession_records.record_for(
        session, edge["newer"], edge["older"], "document", effective if isinstance(effective, str) else None,
        edge.get("detected_by") if isinstance(edge.get("detected_by"), str) else "migration",
    )


def supersession_records_own_work(ctx: MigrationContext) -> list:
    """Declared record files already holding exactly what this step would write: an
    interrupted run wrote them and died before tying the edge. They stay untracked, and
    must not block the run that finishes the job."""
    from artmind import curation_records, supersession_records

    own = []
    try:
        with ctx.session_factory() as session:
            for edge in _legacy_edges(session, ctx.domains):
                record = _mint(session, edge)
                path = curation_records.record_path(supersession_records.NAME, record["id"])
                if path.is_file() and path.read_bytes() == curation_records.serialize(record):
                    own.append(path)
    except Exception:  # noqa: BLE001 - an unreachable graph is its own blocker
        return []
    return own


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
            record = _mint(session, edge)
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


# Core `restate_validity` closes from an edge only when its end is a non-empty string.
_STRING_END = "e IS :: STRING AND e <> ''"


def _superseded_open(domains: list[str] | None) -> str:
    # The end a document-scope edge writes: as `supersession_records._DOCUMENT_EDGES`, which
    # skips an edge with no `record_id` (`supersession_records` step mints them first) and an
    # end that is not a string. Only prefixed dates count: `document_dates` folds the newer
    # side of every edge first, `partial_dates` makes a legacy `effective` a full date.
    return (
        "MATCH (newer)-[s:SUPERSEDES {scope: 'document'}]->(older) "
        f"WHERE (older:Document OR older:DocumentHistory){_scope('older', domains)} "
        "AND s.record_id IS NOT NULL "
        "WITH older, coalesce(s.effective, CASE WHEN newer._valid_precision = 'none' THEN null "
        "ELSE newer._valid_from END) AS e "
        f"WHERE {_STRING_END} "
        f"WITH DISTINCT older WHERE {_open_node()} "
    )


ESTIMATE = "documents (after earlier steps)"


def _superseded_estimate(domains: list[str] | None) -> str:
    """As `_superseded_open`, but read before the earlier steps ran: any document-scope
    edge (record or not) whose end resolves from the unprefixed date too, an integer or
    date `effective` read as the string `partial_dates` will make it."""
    return (
        "MATCH (newer)-[s:SUPERSEDES {scope: 'document'}]->(older) "
        f"WHERE (older:Document OR older:DocumentHistory){_scope('older', domains)} "
        "WITH older, CASE WHEN s.effective IS NULL THEN CASE WHEN newer._valid_precision = 'none' THEN null "
        "ELSE coalesce(newer._valid_from, newer.valid_from) END "
        "WHEN s.effective IS :: STRING THEN s.effective "
        f"WHEN toString(s.effective) =~ '{DATE_SHAPE}' THEN toString(s.effective) END AS e "
        f"WHERE {_STRING_END} "
        f"WITH DISTINCT older WHERE {_open_node()} "
    )


def supersession_ends_pending(ctx: MigrationContext) -> dict:
    """What `restate_validity` would close now, and an estimate of what it will close once
    the earlier steps have folded dates and minted records (on an unmigrated graph the
    first count is often 0). Both the dry run and the real run's effect show the estimate."""
    with ctx.session_factory() as session:
        n = _count(session, _superseded_open(ctx.domains) + "RETURN count(older) AS n", domains=ctx.domains)
        estimate = _count(session, _superseded_estimate(ctx.domains) + "RETURN count(older) AS n",
                          domains=ctx.domains)
    out = {"documents": n} if n else {}
    if estimate and estimate != n:
        out[ESTIMATE] = estimate
    return out


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
        domains: set = set()
        docs: dict[str, set] = {}  # History label -> retired documents with rows still to do
        for label in (*CURRENT_LABELS, *HISTORY_LABELS):
            n = _count(session, _match(label, ctx.domains) + "RETURN count(x) AS n", domains=ctx.domains)
            if not n:
                continue
            applied[label] = n
            domains |= {r.get("domain") for r in session.run(
                _match(label, ctx.domains) + "RETURN DISTINCT x._domain AS domain", domains=ctx.domains).data()}
            if label in HISTORY_LABELS and tags:
                docs[label] = {r.get("doc") for r in session.run(
                    _match(label, ctx.domains) + "RETURN DISTINCT coalesce(x.doc_id, x.id) AS doc",
                    domains=ctx.domains).data()} & set(tags)
        # Staging is read only for the documents whose chunk-family rows are still to do.
        chunk_docs = set().union(*(docs.get(label, set()) for label in _CHUNK_OF))
        chunks = staged_chunk_ids({d: doc_domains.get(d) for d in chunk_docs})
        _bump(session, domains)
        for label in applied:
            params: dict = {"domains": ctx.domains, "epoch": EPOCH}
            if label in HISTORY_LABELS:
                params["tags"] = {d: tags[d] for d in docs.get(label, set())}
            if label in _CHUNK_OF:
                params["chunks"] = {d: chunks[d] for d in docs.get(label, set()) if d in chunks}
            session.run(
                _match(label, ctx.domains) + _join_doc("x", label)
                + "CALL (x, d) { " + _set(label) + " } IN TRANSACTIONS OF %d ROWS" % BATCH_ROWS,
                **params,
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
