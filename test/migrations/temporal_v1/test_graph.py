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
    assert names == ["document_dates", "supersession_ends", "record_time", "old_indexes"]
    assert all(s.writes(None) == [] for s in steps_mod._graph_steps())  # graph only: no files


# ── document_dates ────────────────────────────────────────────────────────────

def test_dates_pending_counts_both_labels_scoped():
    rec = _rec(n=2)
    assert graph.document_dates_pending(_ctx(rec)) == {"Document": 2, "DocumentHistory": 2}
    labels = [re.search(r"MATCH \(d:(\w+)\)", c).group(1) for c, _ in rec.calls]
    assert labels == ["Document", "DocumentHistory"]
    assert all("d._domain IN $domains" in c and p["domains"] == ["notes"] for c, p in rec.calls)


def test_dates_done_is_empty():
    assert graph.document_dates_pending(_ctx(_rec(n=0))) == {}


def test_dates_fold_valid_from_and_remove_both_without_folding_valid_to():
    rec = _rec(n=1, rows=[{"domain": "notes"}])
    out = graph.document_dates_apply(_ctx(rec))
    assert out == {"Document": 1, "DocumentHistory": 1}
    writes = _writes(rec)
    assert [re.search(r"MATCH \(d:(\w+)\)", c).group(1) for c, _ in writes] == ["Document", "DocumentHistory"]
    for cypher, _ in writes:
        assert "d._valid_from = coalesce(d._valid_from, d.valid_from)" in cypher
        assert "REMOVE d.valid_from, d.valid_to" in cypher
        # A supersession's end is never a stated end: no `_valid_to` is written here.
        assert "_valid_to =" not in cypher
        # Only rows still holding an unprefixed name: a second run matches nothing.
        assert "(d.valid_from IS NOT NULL OR d.valid_to IS NOT NULL)" in cypher
        assert "IN TRANSACTIONS OF 5000 ROWS" in cypher
    bumps = rec.queries_containing("MERGE (s:DomainState")
    assert [p["domains"] for _, p in bumps] == [["notes"]]


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
    assert "coalesce(newer._valid_from, newer.valid_from)" in cypher


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
    assert graph._retired_by("DocChunkHistory") == "coalesce($tags[coalesce(x.doc_id, x.id)], 'version:unknown')"


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
    tags = graph.retirement_tags(rec, ["notes"])
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
    assert params["tags"] == {"doc-r": "retire"}


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
        "document_dates", "supersession_ends", "record_time", "old_indexes"]
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
