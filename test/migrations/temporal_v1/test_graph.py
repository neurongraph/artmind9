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
    assert names == ["document_dates", "partial_dates", "supersession_records", "supersession_ends",
                     "record_time", "old_indexes"]
    # graph-only steps write no files; supersession_records declares its record files
    assert all(s.writes(None) == [] for s in steps_mod._graph_steps() if s.name != "supersession_records")


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


# ── supersession_ends ─────────────────────────────────────────────────────────

def test_ends_pending_counts_superseded_documents_with_an_open_node():
    rec = _rec(n=3)
    assert graph.supersession_ends_pending(_ctx(rec)) == {"documents": 3}
    cypher = rec.calls[0][0]
    assert "SUPERSEDES {scope: 'document'}" in cypher and "s.record_id IS NOT NULL" in cypher
    assert "older._valid_to IS NULL" in cypher and "older._domain IN $domains" in cypher
    for label in ("Observation", "ObservationHistory", "DocChunk", "DocChunkHistory", "DocPassage",
                  "DocPassageHistory", "DocSummary", "DocSummaryHistory"):
        assert f"MATCH (x:{label} {{doc_id: older.id}}) WHERE x._valid_to IS NULL" in cypher
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
            return [{"id": "doc-r", "lifecycle_record": "m", "records": []}]
        return [{"domain": "notes"}]
    rec = WritableRecorder(respond)
    graph.record_time_apply(_ctx(rec))
    (cypher, params), = _writes(rec)
    assert params["tags"] == {"doc-r": "retire"} and params["chunks"] == {}


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

    def __init__(self, left, fail_once=False):
        self.left, self.fail_once, self.calls = dict(left), fail_once, []

    def _kind(self, cypher):
        if cypher.startswith("SHOW INDEXES"):
            return "old_indexes"
        if "SUPERSEDES {scope: 'document'}" in cypher and "older" in cypher:
            return "supersession_ends"
        if "d.valid_from IS NOT NULL" in cypher:
            return "document_dates"
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
        if "AS n" in cypher:
            n = self.left[kind] if "d.ingested_at IS NULL" not in cypher else 0
            return FakeResult([{"n": n}])
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
    rec = WritableRecorder(lambda c, p: [{"n": 3}] if "AS n" in c else [{"name": "chunk_valid_to"}])
    runner["graph"] = rec
    result = _invoke("--dryRun")
    assert result.exit_code == 0, result.output
    out = json.loads(result.stdout)
    assert [s["name"] for s in out["steps"] if s["pending"]] == [
        "document_dates", "partial_dates", "supersession_records", "supersession_ends", "record_time",
        "old_indexes"]
    ends = next(s for s in out["steps"] if s["name"] == "supersession_ends")
    assert ends["pending"] == {"documents": 3}  # the estimate equals the count here
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

def test_partial_map_uses_parse_valid(monkeypatch):
    from artmind import temporal

    seen = []
    real = temporal.parse_valid
    monkeypatch.setattr(temporal, "parse_valid", lambda v: seen.append(v) or real(v))
    assert graph.partial_map(["2026", "2026-03", "2026", "9999"]) == {
        "2026": {"start": "2026-01-01", "end": "2027-01-01", "precision": "year"},
        "2026-03": {"start": "2026-03-01", "end": "2026-04-01", "precision": "month"},
        "9999": {"start": "9999-01-01", "end": None, "precision": "year"},  # an open end
    }
    assert seen == ["2026", "2026-03", "9999"]


def test_partial_dates_pending_covers_every_label_and_key():
    rec = _rec(n=2)
    out = graph.partial_dates_pending(_ctx(rec))
    assert set(out) == set(graph.PARTIAL_LABELS) and "Entity" in out
    cypher = rec.calls[0][0]
    for key in ("_valid_from", "_valid_to", "_doc_valid_from", "_doc_valid_to"):
        assert f"toString(x.{key}) =~ '[0-9]{{4}}(-(0[1-9]|1[0-2]))?'" in cypher


def test_partial_dates_apply_normalises_with_the_map_bumping_first():
    def respond(cypher, params):
        if "collect(DISTINCT v)" in cypher:
            return [{"n": 1, "values": ["2026-03"], "domains": ["notes"]}] if "(x:Observation)" in cypher else [
                {"n": 0, "values": [], "domains": []}]
        return []
    rec = WritableRecorder(respond)
    assert graph.partial_dates_apply(_ctx(rec)) == {"Observation": 1}
    (cypher, params), = _writes(rec)
    assert params["map"] == {"2026-03": {"start": "2026-03-01", "end": "2026-04-01", "precision": "month"}}
    assert "coalesce(x._valid_precision, valid_from.precision)" in cypher
    assert "x._valid_from = CASE WHEN valid_from IS NULL THEN x._valid_from ELSE valid_from.start END" in cypher
    assert "x._valid_to = CASE WHEN valid_to IS NULL THEN x._valid_to ELSE valid_to.end END" in cypher
    assert "x._doc_valid_to = CASE WHEN doc_valid_to IS NULL THEN x._doc_valid_to ELSE doc_valid_to.end END" in cypher
    bump = next(i for i, (c, _) in enumerate(rec.calls) if "MERGE (s:DomainState" in c)
    assert bump < rec.calls.index((cypher, params))


def test_partial_dates_second_run_sends_no_writes():
    rec = WritableRecorder(lambda c, p: [{"n": 0, "values": [], "domains": []}] if "collect" in c else [{"n": 0}])
    assert graph.partial_dates_pending(_ctx(rec)) == {}
    assert graph.partial_dates_apply(_ctx(rec)) == {}
    assert_no_write_cypher(rec)


def test_partial_dates_dry_run_sends_only_reads():
    rec = _rec(n=4)
    ctx = _ctx(rec)
    ctx.dry_run = True
    assert graph.partial_dates_pending(ctx)
    assert graph.partial_dates_apply(ctx, True) == {}
    assert_no_write_cypher(rec)


# ── the dry run's estimate of supersession ends ───────────────────────────────

def test_the_dry_run_estimates_ends_the_earlier_steps_will_open_up():
    def respond(cypher, params):
        if "s.record_id IS NOT NULL" in cypher:
            return [{"n": 0}]  # nothing yet: dates unfolded, records not minted
        return [{"n": 2}]
    rec = WritableRecorder(respond)
    ctx = _ctx(rec)
    assert graph.supersession_ends_pending(ctx) == {}  # a real run's pending is the real count
    ctx.dry_run = True
    assert graph.supersession_ends_pending(ctx) == {"documents (after earlier steps)": 2}
    estimate = rec.calls[-1][0]
    assert "coalesce(newer._valid_from, newer.valid_from)" in estimate and "record_id" not in estimate
