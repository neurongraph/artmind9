"""The pack's graph steps (plan Task 33): documents' unprefixed dates, supersession
ends, approximate record time and the old indexes. Recorder only; invented ids."""
import json
import re
from contextlib import contextmanager

import pytest
from click.testing import CliRunner

from _dry_run import WritableRecorder, assert_no_write_cypher
from artmind import backfill, data_model
from artmind.migrations.temporal_v1 import graph
from artmind.migrations.temporal_v1 import steps as steps_mod
from artmind.migrations.temporal_v1.steps import MigrationContext


def _ctx(recorder, domains=("notes",)):
    @contextmanager
    def factory():
        yield recorder
    return MigrationContext(domains=list(domains), session_factory=factory)


def _rec(n=0, rows=None):
    """A recorder answering every count with `n` and every other read with `rows`."""
    def respond(cypher, params):
        if "AS n" in cypher:
            return [{"n": n}]
        return list(rows or [])
    return WritableRecorder(respond)


def _writes(recorder):
    return [(c, p) for c, p in recorder.calls if "IN TRANSACTIONS" in c]


# ── step order ────────────────────────────────────────────────────────────────

def test_the_steps_are_registered_dates_before_ends():
    names = [s.name for s in steps_mod._graph_steps()]
    assert names == ["document_dates", "partial_dates", "record_effective", "chunk_ends",
                     "supersession_records", "supersession_ends", "record_time", "old_indexes"]
    # graph-only steps write no files and do not travel; the record steps declare their files
    assert all(s.writes(None) == [] for s in steps_mod._graph_steps()
               if s.name not in ("supersession_records", "record_effective"))
    assert [s.name for s in steps_mod._graph_steps() if s.travels] == ["record_effective"]


# ── document_dates ────────────────────────────────────────────────────────────

def test_dates_pending_counts_both_labels_scoped():
    rec = _rec(n=2)
    assert graph.document_dates_pending(_ctx(rec)) == {"Document": 2, "DocumentHistory": 2}
    labels = [re.search(r"MATCH \(d:(\w+)\)", c).group(1) for c, _ in rec.calls]
    assert labels == ["Document", "DocumentHistory"]
    assert all("d._domain IN $domains" in c and p["domains"] == ["notes"] for c, p in rec.calls)


def test_dates_done_is_empty():
    assert graph.document_dates_pending(_ctx(_rec(n=0))) == {}


def test_dates_fold_valid_from_and_remove_both_without_folding_valid_to(monkeypatch):
    from artmind import temporal

    monkeypatch.setattr(temporal, "load_schema", lambda d: {"temporal": {"defaults": {"time_source": "filed"}}}
                        if d == "notes" else {})
    rec = _rec(n=1, rows=[{"domain": "notes"}, {"domain": "other"}])
    out = graph.document_dates_apply(_ctx(rec))
    assert out == {"Document": 1, "DocumentHistory": 1}
    writes = _writes(rec)
    assert [re.search(r"MATCH \(d:(\w+)\)", c).group(1) for c, _ in writes] == ["Document", "DocumentHistory"]
    for cypher, params in writes:
        assert "d._valid_from = coalesce(d._valid_from, d.valid_from)" in cypher
        assert "REMOVE d.valid_from, d.valid_to" in cypher
        # A supersession's end is never a stated end: no `_valid_to` is written here.
        assert "_valid_to =" not in cypher
        # Only rows still to do: a second run matches nothing.
        assert ("(d.valid_from IS NOT NULL OR d.valid_to IS NOT NULL "
                "OR (d._valid_precision IS NULL AND d._valid_from IS NOT NULL "
                "AND NOT toString(d._valid_from) =~ '[0-9]{4}(-(0[1-9]|1[0-2]))?'))") in cypher
        # A default date is undated; an old full date is read as a day.
        assert ("WHEN d._valid_time_source = coalesce($sources[coalesce(d._domain, '')], 'default_ingestion') "
                "THEN 'none'") in cypher
        # a full date reads as a day; a partial one is left to partial_dates
        assert "ELSE 'day' END" in cypher and "'year'" not in cypher
        assert "AND NOT toString(d._valid_from) =~ '[0-9]{4}(-(0[1-9]|1[0-2]))?'" in cypher.split("WITH d WHERE")[1]
        assert params["sources"] == {"notes": "filed", "other": "default_ingestion"}
        assert "IN TRANSACTIONS OF 5000 ROWS" in cypher
    bumps = rec.queries_containing("MERGE (s:DomainState")
    assert [p["domains"] for _, p in bumps] == [["notes", "other"]]
    # bumped before the batched writes commit
    first_write = next(i for i, (c, _) in enumerate(rec.calls) if "IN TRANSACTIONS" in c)
    assert rec.calls.index(bumps[0]) < first_write


def test_dates_under_a_domain_also_fold_the_newer_side_of_a_supersession():
    rec = _rec(n=1)
    graph.document_dates_pending(_ctx(rec))
    cypher = rec.calls[0][0]
    assert ("OR EXISTS { MATCH (d)-[:SUPERSEDES {scope: 'document'}]->(o) WHERE (o._domain IN $domains") in cypher


def test_an_unscoped_run_has_no_domain_predicate():
    rec = _rec(n=1, rows=[])
    ctx = _ctx(rec)
    ctx.domains = None
    for step in steps_mod._graph_steps():
        step.pending(ctx)
    assert rec.calls and not [c for c, _ in rec.calls if "$domains" in c]


def test_dates_dry_run_apply_sends_nothing():
    rec = _rec(n=1)
    assert graph.document_dates_apply(_ctx(rec), True) == {}
    assert rec.calls == []


# ── chunk_ends ────────────────────────────────────────────────────────────────

def _chunk_label(cypher):
    return re.search(r"MATCH \([xd]:(\w+)\)", cypher).group(1)


def test_chunk_ends_pending_counts_each_label_of_the_family():
    def respond(cypher, params):
        return [{"n": 7 if _chunk_label(cypher) in ("DocChunk", "DocSummaryHistory") else 0}]
    rec = WritableRecorder(respond)
    assert graph.chunk_ends_pending(_ctx(rec)) == {"DocChunk": 7, "DocSummaryHistory": 7}
    assert {_chunk_label(c) for c, _ in rec.calls} == {"Document", "DocumentHistory", "Observation",
                                                       "ObservationHistory", *graph.CHUNK_FAMILY}
    cypher, params = next((c, p) for c, p in rec.calls if "(x:DocPassage)" in c)
    assert "x._valid_to IS NULL AND x._valid_to_by IS NULL" in cypher
    assert "(d1:Document {id: x.doc_id})" in cypher and "(d2:DocumentHistory {id: x.doc_id})" in cypher
    # a stated end only: never a supersession end (marked by `_valid_to_by`)
    assert "d._valid_to IS NOT NULL AND d._valid_to_by IS NULL" in cypher
    assert "domain" in cypher and params["domains"] == ["notes"]


def test_chunk_ends_unscoped_has_no_domain_predicate():
    rec = _rec(n=0)
    graph.chunk_ends_pending(MigrationContext(
        domains=None, session_factory=_ctx(rec).session_factory))
    assert all("$domains" not in c for c, _ in rec.calls)


def test_chunk_ends_apply_copies_the_documents_end_bumping_first():
    def respond(cypher, params):
        if "AS n" in cypher:
            return [{"n": 3 if _chunk_label(cypher) in ("DocChunk", "DocPassageHistory") else 0}]
        if "AS domain" in cypher:
            return [{"domain": "notes"}]
        return []
    rec = WritableRecorder(respond)
    assert graph.chunk_ends_apply(_ctx(rec)) == {"DocChunk": 3, "DocPassageHistory": 3}
    writes = _writes(rec)
    assert {_chunk_label(c) for c, _ in writes} == {"DocChunk", "DocPassageHistory"}
    for cypher, params in writes:
        assert "SET x._valid_to = d._valid_to" in cypher and "IN TRANSACTIONS OF 5000 ROWS" in cypher
        assert "x._doc_valid_to = coalesce(x._doc_valid_to, d._valid_to)" in cypher
        assert "x._valid_to IS NULL AND x._valid_to_by IS NULL" in cypher.split("CALL")[0]
        assert params["domains"] == ["notes"]
    bump = next(i for i, (c, _) in enumerate(rec.calls) if "MERGE (s:DomainState" in c)
    assert bump < rec.calls.index(writes[0])


def test_chunk_ends_gives_a_document_with_a_stated_end_its_own_copy():
    def respond(cypher, params):
        if "AS n" in cypher:
            return [{"n": 2 if _chunk_label(cypher) == "DocumentHistory" else 0}]
        if "AS domain" in cypher:
            return [{"domain": "notes"}]
        return []
    rec = WritableRecorder(respond)
    assert graph.chunk_ends_apply(_ctx(rec)) == {"DocumentHistory": 2}
    (cypher, params), = _writes(rec)
    assert cypher.startswith("MATCH (d:DocumentHistory)")
    # a stated end only (no supersession marker), and only where no copy exists yet
    assert "d._valid_to IS NOT NULL AND d._valid_to_by IS NULL AND d._doc_valid_to IS NULL" in cypher
    assert "SET d._doc_valid_to = d._valid_to" in cypher and params["domains"] == ["notes"]


def test_chunk_ends_gives_a_document_dated_observation_the_documents_end_and_its_copy():
    def respond(cypher, params):
        if "AS n" in cypher:
            return [{"n": 4 if _chunk_label(cypher) == "Observation" else 0}]
        if "AS domain" in cypher:
            return [{"domain": "notes"}]
        return []
    rec = WritableRecorder(respond)
    assert graph.chunk_ends_apply(_ctx(rec)) == {"Observation": 4}
    (cypher, params), = _writes(rec)
    head = cypher.split("CALL")[0]
    # pending only while the copy is missing: a second run finds nothing
    assert "x._doc_valid_to IS NULL AND x._valid_to_by IS NULL" in head
    # never a fact dated by its own property, never a table row
    assert "NOT coalesce(x._valid_time_source, '') IN ['property', 'table', 'table_rows']" in head
    assert "coalesce(x.source_kind, '') <> 'table'" in head
    assert "d._valid_to IS NOT NULL AND d._valid_to_by IS NULL" in head
    assert "(x._valid_to IS NULL OR x._valid_to = d._valid_to)" in head
    assert "SET x._valid_to = d._valid_to, x._doc_valid_to = d._valid_to" in cypher
    assert params["domains"] == ["notes"]


def test_chunk_ends_second_run_and_dry_run_send_no_writes():
    rec = _rec(n=0)
    assert graph.chunk_ends_pending(_ctx(rec)) == {}
    assert graph.chunk_ends_apply(_ctx(rec)) == {}
    assert_no_write_cypher(rec)
    rec = _rec(n=4)
    assert graph.chunk_ends_apply(_ctx(rec), True) == {}
    assert_no_write_cypher(rec)


# ── supersession_ends ─────────────────────────────────────────────────────────

def test_ends_pending_counts_superseded_documents_with_an_open_node():
    rec = _rec(n=3)
    assert graph.supersession_ends_pending(_ctx(rec)) == {"documents": 3}
    cypher = rec.calls[0][0]
    assert "SUPERSEDES {scope: 'document'}" in cypher and "s.record_id IS NOT NULL" in cypher
    assert "older._valid_to IS NULL OR older._valid_to > e" in cypher and "older._domain IN $domains" in cypher
    for label in ("Observation", "ObservationHistory"):   # only a document-dated one (it carries the copy)
        assert (f"MATCH (x:{label} {{doc_id: older.id}}) WHERE x._valid_to IS NULL "
                "OR (x._doc_valid_to IS NOT NULL AND x._valid_to > e) }") in cypher
    # a document-level node whose stated end is later than the supersession's is still to close
    for label in ("DocChunk", "DocChunkHistory", "DocPassage", "DocPassageHistory", "DocSummary",
                  "DocSummaryHistory"):
        assert f"MATCH (x:{label} {{doc_id: older.id}}) WHERE x._valid_to IS NULL OR x._valid_to > e }}" in cypher
    # only prefixed dates count: document_dates folds the newer side first
    assert "ELSE newer._valid_from END" in cypher and "newer.valid_from" not in cypher


def test_ends_apply_restates_each_superseded_document(monkeypatch):
    from artmind import projection, supersession_records

    restated, rebuilt = [], []
    monkeypatch.setattr(supersession_records, "restate_validity", lambda tx, older: restated.append(older) or 2)
    monkeypatch.setattr(projection, "keys_for_document",
                        lambda tx, doc_id, status=None: {("k", "THING", "notes")} if doc_id == "doc-b" else set())
    monkeypatch.setattr(projection, "rebuild", lambda tx, keys, **kw: rebuilt.append(set(keys)))
    rec = _rec(rows=[{"id": "doc-b"}, {"id": "doc-a"}])
    out = graph.supersession_ends_apply(_ctx(rec))
    assert restated == ["doc-a", "doc-b"] and out == {"documents": 2, "nodes_closed": 4}
    assert rebuilt == [{("k", "THING", "notes")}]
    bumps = rec.queries_containing("MERGE (s:DomainState")
    assert sorted(p["doc_id"] for _, p in bumps) == ["doc-a", "doc-b"]


def test_ends_apply_with_nothing_superseded_writes_nothing(monkeypatch):
    rec = _rec(rows=[])
    assert graph.supersession_ends_apply(_ctx(rec)) == {}
    assert len(rec.calls) == 1


# ── record_time ───────────────────────────────────────────────────────────────

def _label(cypher):
    return re.search(r"MATCH \(x:(\w+)\)", cypher).group(1)


def test_record_time_pending_counts_per_label_and_unknown():
    def respond(cypher, params):
        if "d.ingested_at IS NULL" in cypher:
            return [{"n": 1 if _label(cypher) == "DocChunkHistory" else 0}]
        return [{"n": 4 if _label(cypher) in ("Observation", "DocChunkHistory") else 0}]
    rec = WritableRecorder(respond)
    assert graph.record_time_pending(_ctx(rec)) == {
        "Observation": 4, "DocChunkHistory": 4, "unknown": {"DocChunkHistory": 1}}


def test_record_time_sets_current_and_history_stamps_guarded():
    rec = _rec(n=5, rows=[])
    out = graph.record_time_apply(_ctx(rec))
    assert set(out) == {*graph.CURRENT_LABELS, *graph.HISTORY_LABELS}
    writes = {_label(c): (c, p) for c, p in _writes(rec)}
    assert set(writes) == set(out)
    for label in graph.CURRENT_LABELS:
        cypher, params = writes[label]
        assert "x._recorded_at IS NULL" in cypher.split("CALL")[0]
        assert "SET x._recorded_at = coalesce(d.ingested_at, $epoch), x._record_time_approx = true" in cypher
        assert params["epoch"] == "1970-01-01T00:00:00+00:00"
    for label in graph.HISTORY_LABELS:
        cypher, params = writes[label]
        assert "(x._retired_at IS NULL OR x._retired_by IS NULL)" in cypher.split("CALL")[0]
        assert "x._retired_at = coalesce(x._retired_at, d.ingested_at, $epoch)" in cypher
        assert "x._recorded_at = coalesce(x._recorded_at, $epoch)" in cypher
        assert "x._retired_by = coalesce(x._retired_by," in cypher
        assert "x._record_time_approx = true" in cypher
        assert "IN TRANSACTIONS OF 5000 ROWS" in cypher
    # A row of a document joins its document; a document is its own.
    assert "OPTIONAL MATCH (d1:Document {id: x.doc_id})" in writes["ObservationHistory"][0]
    assert "WITH x, x AS d" in writes["DocumentHistory"][0]


def test_retired_by_rules_in_cypher():
    obs = graph._retired_by("ObservationHistory")
    # an observation of the document's own version takes the tag; an earlier version n
    # takes version:<n + 1>; no version is unknown
    assert "WHEN x.doc_version IS NULL THEN 'version:unknown'" in obs
    assert "toInteger(x.doc_version) >= toInteger(d.version)) THEN $tags[coalesce(x.doc_id, x.id)]" in obs
    assert "THEN 'version:' + toString(toInteger(x.doc_version) + 1)" in obs
    assert obs.endswith("ELSE 'version:unknown' END")
    chunk = graph._retired_by("DocChunkHistory")
    assert chunk == ("CASE WHEN $tags[coalesce(x.doc_id, x.id)] IS NULL THEN 'version:unknown' "
                     "WHEN $chunks[coalesce(x.doc_id, x.id)] IS NULL OR x.id IS NULL "
                     "OR x.id IN $chunks[coalesce(x.doc_id, x.id)] THEN $tags[coalesce(x.doc_id, x.id)] "
                     "ELSE 'version:unknown' END")
    assert "x.chunk_id IN $chunks" in graph._retired_by("DocPassageHistory")
    assert "x.chunk_id IN $chunks" in graph._retired_by("DocSummaryHistory")
    assert graph._retired_by("DocumentHistory") == "coalesce($tags[coalesce(x.doc_id, x.id)], 'version:unknown')"


@pytest.fixture
def curation(monkeypatch, tmp_path):
    import paths

    monkeypatch.setattr(paths, "CURATION_DIR", tmp_path / "curation")
    return tmp_path / "curation"


def _lifecycle(curation, doc_id, **extra):
    from artmind import lifecycle_records

    folder = curation / "lifecycle"
    folder.mkdir(parents=True, exist_ok=True)
    rid = lifecycle_records.record_id(doc_id)
    (folder / f"{rid}.json").write_text(json.dumps({"id": rid, "doc_id": doc_id, "status": "retired", **extra}))


def test_retirement_tags_from_records_edges_and_marks(curation):
    _lifecycle(curation, "doc-rec", retired_by="supersede:r9")
    _lifecycle(curation, "doc-old")  # a record from before `retired_by`
    rows = [
        {"id": "doc-rec", "lifecycle_record": None, "records": ["r1"]},
        {"id": "doc-edge", "lifecycle_record": None, "records": ["r2", "r3"]},
        {"id": "doc-old", "lifecycle_record": None, "records": []},
        {"id": "doc-mark", "lifecycle_record": "x", "records": []},
        {"id": "doc-gone", "lifecycle_record": None, "records": []},
    ]
    rec = WritableRecorder(lambda c, p: rows)
    tags, domains = graph.retirement_tags(rec, ["notes"])
    assert set(domains) == set(tags)
    assert tags == {"doc-rec": "supersede:r9", "doc-edge": "supersede:r2", "doc-old": "retire", "doc-mark": "retire"}
    cypher, params = rec.calls[0]
    assert "MATCH (d:DocumentHistory)" in cypher and "d._domain IN $domains" in cypher
    assert "ORDER BY coalesce(s.effective, '') DESC, n.id DESC" in cypher


def test_record_time_passes_the_tags(curation):
    def respond(cypher, params):
        if "AS n" in cypher:
            return [{"n": 1 if _label(cypher) == "ObservationHistory" else 0}]
        if "MATCH (d:DocumentHistory)" in cypher:
            return [{"id": "doc-r", "domain": "notes", "lifecycle_record": "m", "records": []},
                    {"id": "doc-s", "domain": "notes", "lifecycle_record": "m", "records": []}]
        if "AS doc" in cypher:
            return [{"doc": "doc-r"}]  # only doc-r has rows still to do
        return [{"domain": "notes"}]
    rec = WritableRecorder(respond)
    graph.record_time_apply(_ctx(rec))
    (cypher, params), = _writes(rec)
    # tags only for the documents with rows to do; no $chunks on an observation statement
    assert params["tags"] == {"doc-r": "retire"} and "chunks" not in params


def test_chunks_go_only_to_chunk_family_statements_per_document(curation, monkeypatch):
    monkeypatch.setattr(graph, "staged_chunk_ids",
                        lambda docs: {d: [f"{d}_001"] for d in docs if d is not None})

    def respond(cypher, params):
        if "AS n" in cypher:
            return [{"n": 1 if _label(cypher) in ("DocChunkHistory", "ObservationHistory") else 0}]
        if "MATCH (d:DocumentHistory)" in cypher:
            return [{"id": "doc-r", "domain": "notes", "lifecycle_record": "m", "records": []},
                    {"id": "doc-s", "domain": "notes", "lifecycle_record": "m", "records": []}]
        if "AS doc" in cypher:
            return [{"doc": "doc-r"}] if _label(cypher) == "DocChunkHistory" else [{"doc": "doc-s"}]
        return [{"domain": "notes"}]
    rec = WritableRecorder(respond)
    graph.record_time_apply(_ctx(rec))
    writes = {_label(c): p for c, p in _writes(rec)}
    assert writes["DocChunkHistory"]["chunks"] == {"doc-r": ["doc-r_001"]}
    assert writes["DocChunkHistory"]["tags"] == {"doc-r": "retire"}
    assert "chunks" not in writes["ObservationHistory"] and writes["ObservationHistory"]["tags"] == {"doc-s": "retire"}


def test_staged_chunk_ids_read_the_latest_chunks_json(monkeypatch, tmp_path):
    import paths

    monkeypatch.setattr(paths, "KG_DIR", tmp_path / "kg")
    folder = tmp_path / "kg" / "notes" / "note-a~abc"
    folder.mkdir(parents=True)
    (folder / "document.json").write_text(json.dumps({"id": "doc-a"}))
    (folder / "chunks.json").write_text(json.dumps([{"id": "doc-a_001"}, {"id": "doc-a_002"}]))
    other = tmp_path / "kg" / "notes" / "note-b"
    other.mkdir()
    (other / "document.json").write_text(json.dumps({"id": "doc-b"}))  # no chunks.json
    assert graph.staged_chunk_ids({"doc-a": "notes", "doc-b": "notes", "doc-c": "gone"}) == {
        "doc-a": ["doc-a_001", "doc-a_002"]}


# ── supersession_records ──────────────────────────────────────────────────────

def test_legacy_edges_get_a_record_file_then_the_edge_is_tied(curation, monkeypatch):
    from artmind import curation_records, supersession_records

    def respond(cypher, params):
        if "AS n" in cypher and "s.record_id IS NULL" in cypher and "RETURN count(s)" in cypher:
            return [{"n": 1}]
        if "RETURN newer.id AS newer" in cypher:
            return [{"newer": "doc-new", "older": "doc-old", "effective": "2025-03-01", "detected_by": None}]
        if "RETURN DISTINCT d._domain" in cypher:
            return [{"domain": "notes"}]
        return [{"n": 1}]
    rec = WritableRecorder(respond)
    ctx = _ctx(rec)
    assert graph.supersession_records_pending(ctx) == {"edges": 1}
    rid = supersession_records.record_id("doc-new", "doc-old", "document")
    path = curation_records.record_path(supersession_records.NAME, rid)
    assert graph.supersession_records_writes(ctx) == [path]
    rec.calls.clear()
    assert graph.supersession_records_apply(ctx) == {"records": 1}
    record = json.loads(path.read_text())
    assert (record["newer_doc_id"], record["older_doc_id"], record["scope"], record["effective"]) == (
        "doc-new", "doc-old", "document", "2025-03-01")
    assert record["detected_by"] == "migration" and record["domains"] == ["notes"]
    (tie, params), = rec.queries_containing("SET s.record_id = $id")
    assert "WHERE s.record_id IS NULL" in tie
    assert params["id"] == rid and params["fingerprint"] == curation_records.fingerprint(path.read_bytes())
    assert (params["newer"], params["older"], params["effective"]) == ("doc-new", "doc-old", "2025-03-01")


def test_no_legacy_edges_is_done_and_writes_nothing(curation):
    rec = WritableRecorder(lambda c, p: [{"n": 0}] if "AS n" in c else [])
    assert graph.supersession_records_pending(_ctx(rec)) == {}
    assert graph.supersession_records_apply(_ctx(rec)) == {}
    assert not curation.exists()


# ── old_indexes ───────────────────────────────────────────────────────────────

def test_old_indexes_pending_and_drop():
    rec = WritableRecorder(lambda c, p: [{"name": "chunk_valid_to"}, {"name": "entity_valid_from"}]
                           if c.startswith("SHOW INDEXES") else [])
    assert graph.old_indexes_pending(_ctx(rec)) == {"indexes": 2}
    show, params = rec.calls[0]
    assert "properties WHERE p IN ['valid_from', 'valid_to']" in show
    assert set(params["names"]) == {"entity_valid_from", "entity_valid_to", "chunk_valid_from",
                                    "chunk_valid_to", "document_valid_from", "document_valid_to"}
    rec.calls.clear()
    assert graph.old_indexes_apply(_ctx(rec)) == {"indexes": 2}
    assert [c for c, _ in rec.calls[1:]] == ["DROP INDEX chunk_valid_to IF EXISTS",
                                             "DROP INDEX entity_valid_from IF EXISTS"]


def test_old_indexes_gone_is_done():
    assert graph.old_indexes_pending(_ctx(WritableRecorder(lambda c, p: []))) == {}


# ── the runner over the real steps ────────────────────────────────────────────

class Graph:
    """A tiny stateful graph: each step's pending count, and a write that converts
    `per_call` rows per statement (raising once mid-way when `fail_once`)."""

    STEPS = ("document_dates", "partial_dates", "record_effective", "chunk_ends", "supersession_records",
             "supersession_ends",
             "record_time", "old_indexes")

    def __init__(self, left, fail_once=False):
        self.left = {k: 0 for k in self.STEPS}
        self.left.update(left)
        self.fail_once, self.calls = fail_once, []

    def _kind(self, cypher):
        if cypher.startswith("SHOW INDEXES"):
            return "old_indexes"
        if "d._valid_to IS NOT NULL AND d._valid_to_by IS NULL" in cypher:
            return "chunk_ends"
        if "s.record_id IS NULL" in cypher:
            return "supersession_records"
        if "SUPERSEDES {scope: 'document'}]->(older)" in cypher:
            return "supersession_ends"
        if "d.valid_from IS NOT NULL" in cypher:
            return "document_dates"
        if "=~ '[0-9]{4}(-(0[1-9]|1[0-2]))?'" in cypher or "$readable" in cypher:
            return "partial_dates"
        return "record_time"

    def run(self, cypher, **params):
        from _neo4j_recorder import FakeResult

        self.calls.append((cypher, params))
        kind = self._kind(cypher)
        if "IN TRANSACTIONS" in cypher:
            # some batches commit, then the run is interrupted
            converted = min(self.left[kind], 2)
            self.left[kind] -= converted
            if self.fail_once:
                self.fail_once = False
                raise RuntimeError("connection lost mid-batch")
            self.left[kind] = 0
            return FakeResult([])
        if cypher.startswith("DROP INDEX"):
            self.left["old_indexes"] -= 1
            return FakeResult([])
        if kind == "partial_dates" and "AS values" in cypher:
            return FakeResult([{"values": ["2026-03"] if self.left[kind] else []}])
        if "AS n" in cypher:
            # rows with no `ingested_at`, and partial values parse_valid cannot read: none here
            quiet = "d.ingested_at IS NULL" in cypher or (kind == "partial_dates" and "AND NOT" in cypher)
            return FakeResult([{"n": 0 if quiet else self.left[kind]}])
        if kind == "old_indexes":
            return FakeResult([{"name": "chunk_valid_to"}] if self.left[kind] else [])
        return FakeResult([])

    def execute_write(self, fn):
        return fn(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def runner(monkeypatch, tmp_path):
    from artmind.migrations.temporal_v1 import cli as pack_cli

    state = {}

    def factory():
        return state["graph"]
    monkeypatch.setattr(pack_cli, "_open_graph", factory)
    monkeypatch.setattr(pack_cli, "_probe_graph", factory)
    monkeypatch.setattr(pack_cli, "_vault_root", lambda: tmp_path)

    class Out:
        returncode, stdout = 0, ""
    monkeypatch.setattr(pack_cli, "_git", lambda root, *a: Out())
    monkeypatch.setattr(backfill, "worker_is_running", lambda: False)
    monkeypatch.setattr("artmind.cli._get_available_domains", lambda: ["notes"])
    monkeypatch.setattr(data_model, "recorded", lambda: 0)
    monkeypatch.setattr(data_model, "read_graph_version", lambda *a, **k: 0)
    monkeypatch.setattr(steps_mod, "STEPS", steps_mod._graph_steps())
    # supersession_ends has nothing to restate in these graphs (no ids are returned)
    return state


def _invoke(*args):
    from artmind.cli import cli

    return CliRunner().invoke(cli, ["migrate", "temporal", *args, "--compact"])


def test_dry_run_over_the_real_steps_sends_only_reads(runner):
    def respond(cypher, params):
        if "AS n" in cypher:
            return [{"n": 3}]
        if "AS values" in cypher:
            return [{"values": ["2026"]}]
        return [{"name": "chunk_valid_to"}]
    rec = WritableRecorder(respond)
    runner["graph"] = rec
    result = _invoke("--dryRun")
    assert result.exit_code == 0, result.output
    out = json.loads(result.stdout)
    assert [s["name"] for s in out["steps"] if s["pending"]] == [
        "document_dates", "partial_dates", "chunk_ends", "supersession_records", "supersession_ends",
        "record_time", "old_indexes"]  # record_effective: no record files here
    ends = next(s for s in out["steps"] if s["name"] == "supersession_ends")
    assert ends["pending"] == {"documents": 3}  # the estimate equals the count here
    effect = out["effect"]
    assert effect["travels"] is False and effect["reversible"] is False
    assert "not reversible by git" in effect["undo"] and "delete them" in effect["undo"]
    assert "_valid_to_by" in effect["undo"] and "ingest unsupersede" in effect["undo"]
    assert_no_write_cypher(rec)
    assert not [c for c, _ in rec.calls if "IN TRANSACTIONS" in c or c.startswith("DROP")]


def test_an_interrupted_run_resumes_and_a_second_run_is_a_no_op(runner):
    g = Graph({"document_dates": 5, "supersession_ends": 0, "record_time": 0, "old_indexes": 0}, fail_once=True)
    runner["graph"] = g
    first = _invoke()
    out = json.loads(first.stdout)
    assert first.exit_code == 1 and "mid-batch" in out["steps"][0]["error"]
    assert out["steps"][0]["remaining"] == {"Document": 3, "DocumentHistory": 3}  # committed batches stay
    second = _invoke()
    assert second.exit_code == 0, second.output
    assert json.loads(second.stdout)["done"] is True
    g.calls.clear()
    third = _invoke()
    assert third.exit_code == 0
    assert not [c for c, _ in g.calls if "IN TRANSACTIONS" in c or c.startswith("DROP")]


class CrossDomainGraph:
    """Two documents: `doc-old` (domain notes) superseded through an undated edge by
    `doc-new` (domain other), whose date is still unprefixed. A document-dates query
    reaches `doc-new` only through the SUPERSEDES clause of `_doc_scope`."""

    def __init__(self):
        self.new_folded, self.old_open, self.calls = False, True, []

    def run(self, cypher, **params):
        from _neo4j_recorder import FakeResult

        self.calls.append((cypher, params))
        reaches_newer = "EXISTS { MATCH (d)-[:SUPERSEDES" in cypher or "$domains" not in cypher
        if "d._valid_precision IS NULL AND d._valid_from IS NOT NULL" in cypher:  # document_dates
            todo = int(reaches_newer and not self.new_folded and "MATCH (d:Document)" in cypher)
            if "IN TRANSACTIONS" in cypher:
                self.new_folded = self.new_folded or bool(todo)
                return FakeResult([])
            if "AS n" in cypher:
                return FakeResult([{"n": todo}])
            return FakeResult([{"domain": "other"}] if todo else [])
        if "SUPERSEDES {scope: 'document'}]->(older)" in cypher and "s.record_id IS NOT NULL" in cypher:
            open_ = int(self.new_folded and self.old_open)  # the end is the newer's _valid_from
            if "AS n" in cypher:
                return FakeResult([{"n": open_}])
            return FakeResult([{"id": "doc-old"}] if open_ else [])
        if "AS n" in cypher:
            return FakeResult([{"n": 0}])
        return FakeResult([])

    def execute_write(self, fn):
        return fn(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_a_cross_domain_supersession_under_domain_reaches_done(runner, monkeypatch):
    from artmind import projection, supersession_records

    g = CrossDomainGraph()
    runner["graph"] = g

    def restate(tx, older):
        g.old_open = False
        return 1
    monkeypatch.setattr(supersession_records, "restate_validity", restate)
    monkeypatch.setattr(projection, "keys_for_document", lambda tx, doc_id, status=None: set())
    monkeypatch.setattr("artmind.cli._parse_domains", lambda d: ["notes"])
    result = _invoke("--domain", "notes")
    assert result.exit_code == 0, result.output
    out = json.loads(result.stdout)
    assert out["done"] is True
    assert g.new_folded and not g.old_open
    ends = next(s for s in out["steps"] if s["name"] == "supersession_ends")
    assert ends["applied"] == {"documents": 1, "nodes_closed": 1}


# ── partial_dates ─────────────────────────────────────────────────────────────

def test_partial_map_uses_parse_valid_over_strings(monkeypatch):
    from artmind import temporal

    seen = []
    real = temporal.parse_valid
    monkeypatch.setattr(temporal, "parse_valid", lambda v: seen.append(v) or real(v))
    assert graph.partial_map(["2026", "2026-03", 2026, "9999", "0000"]) == {
        "2026": {"start": "2026-01-01", "end": "2027-01-01", "precision": "year"},
        "2026-03": {"start": "2026-03-01", "end": "2026-04-01", "precision": "month"},
        "9999": {"start": "9999-01-01", "end": None, "precision": "year"},  # an open end
    }
    assert seen == ["0000", "2026", "2026-03", "9999"]  # an integer year is read as its string


def _partial_rec(values, todo=2, bad=0, label="Observation"):
    def respond(cypher, params):
        here = f"(x:{label})" in cypher
        if "AS values" in cypher:
            return [{"values": values if here else []}]
        if "AS n" in cypher:
            if not here:
                return [{"n": 0}]
            return [{"n": bad if "AND NOT" in cypher else todo}]
        if "AS domain" in cypher:
            return [{"domain": "notes"}] if here else []
        return []
    return WritableRecorder(respond)


def test_partial_dates_pending_reads_strings_and_reports_unparseable_apart():
    rec = _partial_rec(["2026", "0000"], todo=2, bad=1)
    assert graph.partial_dates_pending(_ctx(rec)) == {"Observation": 2, "unparseable": {"Observation": 1}}
    values_q = next(c for c, _ in rec.calls if "AS values" in c and "(x:Observation)" in c)
    for key in ("_valid_from", "_valid_to", "_doc_valid_from", "_doc_valid_to"):
        assert f"toString(x.{key}) =~ '[0-9]{{4}}(-(0[1-9]|1[0-2]))?'" in values_q
    todo_q, params = next((c, p) for c, p in rec.calls if "AS n" in c and "IN $readable" in c and "AND NOT" not in c)
    assert params["readable"] == ["2026"]
    assert steps_mod.is_done({"unparseable": {"Observation": 1}})  # it never blocks done


def test_partial_dates_apply_normalises_with_a_string_keyed_map_bumping_first():
    rec = _partial_rec(["2026-03"], todo=1)
    assert graph.partial_dates_apply(_ctx(rec)) == {"Observation": 1}
    (cypher, params), = _writes(rec)
    assert params["map"] == {"2026-03": {"start": "2026-03-01", "end": "2026-04-01", "precision": "month"}}
    assert "$map[toString(x._valid_from)]" in cypher and "$map[toString(x._doc_valid_to)]" in cypher
    assert "coalesce(x._valid_precision, valid_from.precision)" in cypher
    assert "x._valid_from = CASE WHEN valid_from IS NULL THEN x._valid_from ELSE valid_from.start END" in cypher
    assert "x._valid_to = CASE WHEN valid_to IS NULL THEN x._valid_to ELSE valid_to.end END" in cypher
    # only rows holding a readable value: an unparseable one is never touched
    assert "toString(x._valid_from) IN $readable" in cypher.split("CALL")[0]
    bump = next(i for i, (c, _) in enumerate(rec.calls) if "MERGE (s:DomainState" in c)
    assert bump < rec.calls.index((cypher, params))


def test_legacy_edge_effective_takes_its_start():
    rec = _partial_rec(["2020"], todo=1, label="never")

    def respond(cypher, params):
        edge = "MATCH ()-[s:SUPERSEDES]->(older)" in cypher
        if "AS values" in cypher:
            return [{"values": ["2020"] if edge else []}]
        if "AS n" in cypher:
            return [{"n": 1 if edge and "AND NOT" not in cypher else 0}]
        return [{"domain": "notes"}] if edge else []
    rec = WritableRecorder(respond)
    assert graph.partial_dates_pending(_ctx(rec)) == {"SUPERSEDES": 1}
    values_q = next(c for c, _ in rec.calls if "AS values" in c and "SUPERSEDES" in c)
    assert "(NOT s.effective IS :: STRING OR toString(s.effective) =~" in values_q
    assert graph.partial_dates_apply(_ctx(rec)) == {"SUPERSEDES": 1}
    (cypher, params), = _writes(rec)
    assert "SET s.effective = e.start" in cypher and "$map[toString(s.effective)]" in cypher
    assert params["map"]["2020"]["start"] == "2020-01-01"


def test_partial_dates_second_run_sends_no_writes():
    rec = _partial_rec([], todo=0)
    assert graph.partial_dates_pending(_ctx(rec)) == {}
    assert graph.partial_dates_apply(_ctx(rec)) == {}
    assert_no_write_cypher(rec)


def test_partial_dates_dry_run_sends_only_reads():
    rec = _partial_rec(["2026"], todo=4)
    ctx = _ctx(rec)
    ctx.dry_run = True
    assert graph.partial_dates_pending(ctx)
    assert graph.partial_dates_apply(ctx, True) == {}
    assert_no_write_cypher(rec)


def test_partial_dates_killed_mid_batch_resume(runner):
    g = Graph({"partial_dates": 5}, fail_once=True)
    runner["graph"] = g
    first = _invoke()
    out = json.loads(first.stdout)
    row = next(s for s in out["steps"] if s["name"] == "partial_dates")
    assert first.exit_code == 1 and "mid-batch" in row["error"]
    assert row["remaining"]["Document"] == 3  # committed batches stay
    second = _invoke()
    assert second.exit_code == 0, second.output
    g.calls.clear()
    assert _invoke().exit_code == 0
    assert not [c for c, _ in g.calls if "IN TRANSACTIONS" in c]


# ── supersession ends: string ends, and the estimate ──────────────────────────

def test_open_ends_use_the_string_check_restate_uses():
    rec = _rec(n=0)
    graph.supersession_ends_pending(_ctx(rec))
    open_q = rec.calls[0][0]
    assert "AS e WHERE e IS :: STRING AND e <> ''" in open_q
    estimate_q = rec.calls[1][0]
    assert "WHEN s.effective IS :: STRING THEN s.effective" in estimate_q
    assert "THEN toString(s.effective) END AS e" in estimate_q and "e IS :: STRING AND e <> ''" in estimate_q


def test_the_estimate_shows_ends_the_earlier_steps_will_open_up():
    def respond(cypher, params):
        if "s.record_id IS NOT NULL" in cypher:
            return [{"n": 0}]  # nothing yet: dates unfolded, records not minted
        return [{"n": 2}]
    rec = WritableRecorder(respond)
    ctx = _ctx(rec)
    assert graph.supersession_ends_pending(ctx) == {"documents (after earlier steps)": 2}  # real run too
    estimate = rec.calls[-1][0]
    assert "coalesce(newer._valid_from, newer.valid_from)" in estimate and "record_id" not in estimate


def test_the_real_run_effect_names_supersession_ends_from_the_estimate(runner):
    g = Graph({"document_dates": 1})
    runner["graph"] = g
    real = Graph.run

    def run(self, cypher, **params):
        if "SUPERSEDES {scope: 'document'}]->(older)" in cypher and "AS n" in cypher \
                and "record_id IS NOT NULL" not in cypher and self.left["document_dates"]:
            from _neo4j_recorder import FakeResult
            return FakeResult([{"n": 1}])  # the estimate, before the fold
        return real(self, cypher, **params)
    g.run = run.__get__(g)
    out = json.loads(_invoke().stdout)
    assert "supersession_ends" in out["effect"]["changes"][0]["sample"]


# ── supersession_records: a killed run resumes over its own record file ───────

class RecordsGraph:
    """One legacy edge doc-new -> doc-old; the tie fails once when `fail_tie`."""

    def __init__(self, fail_tie=True):
        self.tied, self.fail_tie, self.calls = False, fail_tie, []

    def run(self, cypher, **params):
        from _neo4j_recorder import FakeResult

        self.calls.append((cypher, params))
        if "SET s.record_id = $id" in cypher:
            if self.fail_tie:
                self.fail_tie = False
                raise RuntimeError("connection lost before the tie")
            self.tied = True
            return FakeResult([{"n": 1}])
        if "s.record_id IS NULL" in cypher:
            if "RETURN count(s)" in cypher:
                return FakeResult([{"n": 0 if self.tied else 1}])
            return FakeResult([] if self.tied else [
                {"newer": "doc-new", "older": "doc-old", "effective": "2025-03-01", "detected_by": None}])
        if "RETURN DISTINCT d._domain AS domain" in cypher:
            return FakeResult([{"domain": "notes"}])
        if "AS values" in cypher:
            return FakeResult([{"values": []}])
        if "AS n" in cypher:
            return FakeResult([{"n": 0}])
        return FakeResult([])

    def execute_write(self, fn):
        return fn(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_a_record_written_before_a_failed_tie_does_not_block_the_resume(runner, curation, monkeypatch, tmp_path):
    from artmind import supersession_records
    from artmind.migrations.temporal_v1 import cli as pack_cli

    g = RecordsGraph()
    runner["graph"] = g
    monkeypatch.setattr(pack_cli, "_vault_root", lambda: tmp_path)

    class Out:
        def __init__(self, out=""):
            self.returncode, self.stdout = 0, out

    def git(root, args):
        if args[0] != "status":
            return Out()
        # the record file is untracked once written
        found = [p for p in args[2:] if p.endswith(".json") and __import__("pathlib").Path(p).is_file()]
        return Out("".join(f"?? {__import__('os').path.relpath(p, tmp_path)}\n" for p in found))
    monkeypatch.setattr(pack_cli, "_git", git)

    first = _invoke()
    assert first.exit_code == 1, first.output
    row = next(s for s in json.loads(first.stdout)["steps"] if s["name"] == "supersession_records")
    assert "before the tie" in row["error"]
    rid = supersession_records.record_id("doc-new", "doc-old", "document")
    path = curation / "supersessions" / f"{rid}.json"
    assert path.is_file() and not g.tied

    second = _invoke()
    assert second.exit_code == 0, second.output  # its own file does not block
    assert g.tied

    # a file someone changed is not this step's work: it blocks
    g.tied, g.fail_tie = False, False
    path.write_text(path.read_text().replace("2025-03-01", "2025-04-01"))
    third = _invoke()
    assert third.exit_code == 1 and "uncommitted changes" in third.output


# ── record_effective ──────────────────────────────────────────────────────────

def _record_file(curation, rid, effective, domains=("notes",)):
    from artmind import curation_records

    record = {"id": rid, "newer_doc_id": "doc-new", "older_doc_id": "doc-old", "scope": "document",
              "effective": effective, "detected_by": "notice", "domains": list(domains),
              "recorded_at": "2025-01-01T00:00:00+00:00"}
    path = curation / "supersessions" / f"{rid}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(curation_records.serialize(record))
    return path


def test_record_effective_rewrites_to_the_start_and_reties_the_edge(curation):
    from artmind import curation_records

    partial = _record_file(curation, "a" * 40, "2020")
    number = _record_file(curation, "b" * 40, 2021)
    full = _record_file(curation, "c" * 40, "2020-05-01")
    bad = _record_file(curation, "d" * 40, "2020-13-45x")  # not partial: left alone, not counted
    odd = _record_file(curation, "e" * 40, 99999)  # not a string, unreadable
    elsewhere = _record_file(curation, "f" * 40, "2019", domains=("other",))
    untouched = {p: p.read_bytes() for p in (full, bad, odd, elsewhere)}
    rec = WritableRecorder(lambda c, p: [{"n": 1}])
    ctx = _ctx(rec)
    assert graph.record_effective_pending(ctx) == {"records": 2, "unparseable": {"records": 1}}
    assert graph.record_effective_writes(ctx) == [partial, number]
    assert graph.record_effective_apply(ctx) == {"records": 2}
    assert json.loads(partial.read_text())["effective"] == "2020-01-01"
    assert json.loads(number.read_text())["effective"] == "2021-01-01"
    assert all(p.read_bytes() == b for p, b in untouched.items())
    ties = rec.queries_containing("SET s.record_fingerprint = $fingerprint")
    assert [(p["id"], p["effective"]) for _, p in ties] == [("a" * 40, "2020-01-01"), ("b" * 40, "2021-01-01")]
    assert ties[0][1]["fingerprint"] == curation_records.fingerprint(partial.read_bytes())
    # a second run touches nothing
    rec.calls.clear()
    assert graph.record_effective_pending(ctx) == {"unparseable": {"records": 1}}
    assert graph.record_effective_apply(ctx) == {} and rec.calls == []
    assert steps_mod.is_done(graph.record_effective_pending(ctx))


def test_record_effective_unscoped_covers_every_record(curation):
    _record_file(curation, "f" * 40, "2019", domains=("other",))
    ctx = _ctx(WritableRecorder())
    ctx.domains = None
    assert graph.record_effective_pending(ctx) == {"records": 1}


def test_record_effective_dry_run_writes_nothing(curation):
    path = _record_file(curation, "a" * 40, "2020-03")
    before = path.read_bytes()
    rec = WritableRecorder()
    ctx = _ctx(rec)
    ctx.dry_run = True
    assert graph.record_effective_pending(ctx) == {"records": 1}
    assert graph.record_effective_apply(ctx, True) == {}
    assert path.read_bytes() == before and rec.calls == []


# ── _blocked over a real git repository whose top is above the vault ──────────

def test_own_work_is_resolved_from_the_repository_top(monkeypatch, tmp_path):
    import subprocess

    from artmind.migrations.temporal_v1 import cli as pack_cli
    from artmind.migrations.temporal_v1.steps import Step

    repo = tmp_path / "repo"
    vault = repo / "vaults" / "v1"
    (vault / "curation").mkdir(parents=True)
    env_git = ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid"]
    subprocess.run([*env_git, "init", "-q"], cwd=repo, check=True)
    (vault / "keep.txt").write_text("x")
    subprocess.run([*env_git, "add", "."], cwd=repo, check=True)
    subprocess.run([*env_git, "commit", "-q", "-m", "init"], cwd=repo, check=True)
    own = vault / "curation" / "own.json"
    own.write_text("{}")  # untracked: a killed run's own record
    other = vault / "curation" / "other.json"
    other.write_text("{}")  # untracked, not the step's work

    monkeypatch.setattr(pack_cli, "_vault_root", lambda: vault)
    monkeypatch.setattr(pack_cli, "_probe_graph", lambda: WritableRecorder())
    monkeypatch.setattr(backfill, "worker_is_running", lambda: False)
    monkeypatch.setattr(data_model, "MODEL_FILE", vault / "data_model.yaml")
    step = Step("s", lambda c: {}, lambda c, d: {}, writes=lambda c: [own], own_work=lambda c: [own])
    monkeypatch.setattr(steps_mod, "STEPS", [step])
    assert pack_cli._blocked(_ctx(WritableRecorder())) == []
    monkeypatch.setattr(steps_mod, "STEPS", [Step("s", lambda c: {}, lambda c, d: {},
                                                  writes=lambda c: [own, other], own_work=lambda c: [own])])
    reasons = pack_cli._blocked(_ctx(WritableRecorder()))
    assert len(reasons) == 1 and "vaults/v1/curation/other.json" in reasons[0] and "own.json" not in reasons[0]
