"""A retirement travels as a curation record, and sticks (spec 2026-09-26
§15 A10). No Neo4j: recording sessions stand in; every test asserts on the
Cypher and parameters sent (CLAUDE.md §3)."""
import hashlib
import json
from contextlib import contextmanager

import pytest

import artmind.ingest as ing
from artmind import curation_records, lifecycle, lifecycle_records


@pytest.fixture(autouse=True)
def curation_dir(tmp_path, monkeypatch):
    import paths

    target = tmp_path / "curation"
    monkeypatch.setattr(paths, "CURATION_DIR", target)
    return target


class _Result:
    def __init__(self, rows=()):
        self._rows = list(rows)

    def data(self):
        return list(self._rows)

    def single(self):
        return self._rows[0] if self._rows else None

    def __iter__(self):
        return iter(self._rows)


class _Tx:
    """Answers `keys_for_document` with one key, the domain lookup with
    `banking`, and the "did this record retire it?" check with `held`."""

    def __init__(self, held=1, holders=()):
        self.calls = []
        self.held = held
        #: Doc ids whose retirement carries the record (for a lookup by record id).
        self.holders = list(holders)

    def run(self, cypher, **params):
        self.calls.append((cypher, params))
        if "RETURN d._domain AS domain" in cypher:
            return _Result([{"domain": "banking"}])
        if "RETURN count(d) AS n" in cypher and "lifecycle_record" in cypher:
            return _Result([{"n": self.held}])
        if "RETURN d.id AS doc_id" in cypher and "lifecycle_record" in cypher:
            return _Result([{"doc_id": d} for d in self.holders])
        if "o.key AS key" in cypher or "RETURN DISTINCT o.key" in cypher or ".key AS key" in cypher:
            return _Result([{"key": "acme|ORG|banking"}])
        return _Result([{"n": 2}])

    def execute_write(self, fn, *args, **kwargs):
        return fn(self, *args, **kwargs)

    def texts(self):
        return [c for c, _ in self.calls]


@pytest.fixture()
def no_rebuild(monkeypatch):
    import artmind.projection as projection

    seen = []
    monkeypatch.setattr(projection, "rebuild", lambda tx, keys, **kw: seen.append(sorted(keys)) or {})
    return seen


def test_a_lifecycle_record_is_keyed_by_a_hash_of_the_document_id():
    record = lifecycle_records.record_for(_Tx(), "table:banking:accounts")

    assert record == {
        "id": hashlib.sha1(b"table:banking:accounts").hexdigest(),
        "doc_id": "table:banking:accounts", "domain": "banking", "status": "retired",
    }


def test_retire_document_writes_the_record_and_marks_the_document(monkeypatch, curation_dir, no_rebuild):
    tx = _Tx()

    @contextmanager
    def _fake(*a, **k):
        yield tx

    monkeypatch.setattr(lifecycle, "neo4j_session", _fake)

    lifecycle.retire_document("doc-1")

    path = curation_dir / "lifecycle" / f"{lifecycle_records.record_id('doc-1')}.json"
    assert json.loads(path.read_text())["doc_id"] == "doc-1"
    marks = [p for c, p in tx.calls if "SET d.lifecycle_record = $id" in c]
    assert marks == [{"doc_id": "doc-1", "id": lifecycle_records.record_id("doc-1"),
                      "fingerprint": curation_records.fingerprint(path.read_bytes())}]
    assert any("REMOVE d:Document SET d:DocumentHistory" in c for c in tx.texts())
    assert len(no_rebuild) == 1, "retire still rebuilds in its own transaction"


def test_restore_document_deletes_the_record_and_unmarks(monkeypatch, curation_dir, no_rebuild):
    curation_records.write_record("lifecycle", lifecycle_records.record_for(_Tx(), "doc-1"))
    tx = _Tx()

    @contextmanager
    def _fake(*a, **k):
        yield tx

    monkeypatch.setattr(lifecycle, "neo4j_session", _fake)

    lifecycle.restore_document("doc-1")

    assert curation_records.read_record("lifecycle", lifecycle_records.record_id("doc-1")) is None
    assert any("REMOVE d.lifecycle_record, d.lifecycle_fingerprint" in c for c in tx.texts())
    assert any("REMOVE d:DocumentHistory SET d:Document" in c for c in tx.texts())


def test_apply_retires_without_rebuilding_and_returns_the_keys(no_rebuild):
    tx = _Tx()
    record = lifecycle_records.record_for(tx, "doc-1")

    keys = lifecycle_records.apply(tx, record, "fp")

    assert keys == {("acme", "ORG", "banking")}
    assert no_rebuild == [], "vault sync's union rebuild covers it"
    assert any("REMOVE d:Document SET d:DocumentHistory" in c for c in tx.texts())
    assert ("MATCH (d:DocumentHistory {id: $doc_id}) SET d.lifecycle_record = $id, d.lifecycle_fingerprint = $fingerprint",
            {"doc_id": "doc-1", "id": record["id"], "fingerprint": "fp"}) in tx.calls


def test_remove_restores_only_a_document_this_record_retired(no_rebuild):
    record = lifecycle_records.record_for(_Tx(), "doc-1")

    not_ours = _Tx(held=0)
    assert lifecycle_records.remove(not_ours, record) == set()
    assert not any("SET d:Document" in c for c in not_ours.texts())

    ours = _Tx(held=1)
    assert lifecycle_records.remove(ours, record) == {("acme", "ORG", "banking")}
    assert any("REMOVE d:DocumentHistory SET d:Document" in c for c in ours.texts())
    assert any("REMOVE d.lifecycle_record, d.lifecycle_fingerprint" in c for c in ours.texts())


def _stage(folder, doc_id="doc-1"):
    folder.mkdir(parents=True)
    (folder / "document.json").write_text(json.dumps({"id": doc_id, "name": "a.md"}))
    (folder / "observations.json").write_text("[]")
    return folder


def _commit(monkeypatch, folder, defer_rebuild=True):
    import artmind.graph_query as graph_query

    tx = _Tx()

    @contextmanager
    def _fake(*a, **k):
        yield tx

    monkeypatch.setattr(graph_query, "neo4j_session", _fake)
    monkeypatch.setattr(ing, "_ensure_neo4j_schema", lambda *a, **k: None)
    summary = ing._write_to_neo4j(folder, "banking", defer_rebuild=defer_rebuild)
    return tx, summary


def test_a_commit_of_a_retired_document_keeps_it_retired(tmp_path, monkeypatch):
    curation_records.write_record("lifecycle", lifecycle_records.record_for(_Tx(), "doc-1"))

    tx, summary = _commit(monkeypatch, _stage(tmp_path / "doc"))

    texts = tx.texts()
    revive = next(i for i, c in enumerate(texts) if "CREATE (n:Document {id: $id})" in c)
    retire = next(i for i, c in enumerate(texts) if "REMOVE d:Document SET d:DocumentHistory" in c)
    assert retire > revive
    assert summary["retired"] is True


def test_a_commit_retires_before_the_projection_rebuild_it_does_not_defer(tmp_path, monkeypatch):
    """With the rebuild in the same transaction (plain `commit_to_graph`) the
    document must already be in history when the rebuild reads the graph,
    or the aggregates would still count what only it asserted."""
    import artmind.projection as projection

    curation_records.write_record("lifecycle", lifecycle_records.record_for(_Tx(), "doc-1"))
    sequence = []
    monkeypatch.setattr(projection, "rebuild", lambda tx, keys, **kw: sequence.append("rebuild") or {})
    import artmind.graph_query as graph_query

    tx = _Tx()
    real_run = tx.run
    tx.run = lambda cypher, **params: (
        sequence.append("retire" if "REMOVE d:Document SET d:DocumentHistory" in cypher else "other"),
        real_run(cypher, **params),
    )[1]

    @contextmanager
    def _fake(*a, **k):
        yield tx

    monkeypatch.setattr(graph_query, "neo4j_session", _fake)
    monkeypatch.setattr(ing, "_ensure_neo4j_schema", lambda *a, **k: None)
    summary = ing._write_to_neo4j(_stage(tmp_path / "doc"), "banking", defer_rebuild=False)

    assert summary["retired"] is True
    assert sequence.count("retire") == 1 and sequence.count("rebuild") == 1
    assert sequence.index("retire") < sequence.index("rebuild")


def test_committing_a_user_chat_never_retires_it_even_with_a_record(tmp_path, monkeypatch):
    curation_records.write_record("lifecycle", lifecycle_records.record_for(_Tx(), "doc-1"))
    folder = _stage(tmp_path / "chat")
    (folder / "document.json").write_text(json.dumps({"id": "doc-1", "name": "chat", "source_kind": ing.USER_CHAT_SOURCE_KIND}))

    tx, summary = _commit(monkeypatch, folder)

    assert not any("REMOVE d:Document SET d:DocumentHistory" in c for c in tx.texts())
    assert "retired" not in summary


def test_a_commit_of_a_live_document_does_not_retire_it(tmp_path, monkeypatch):
    tx, summary = _commit(monkeypatch, _stage(tmp_path / "doc"))

    assert not any("REMOVE d:Document SET d:DocumentHistory" in c for c in tx.texts())
    assert "retired" not in summary


def test_retracting_a_document_clears_its_lifecycle_mark(monkeypatch):
    import artmind.graph_query as graph_query
    import artmind.projection as projection

    tx = _Tx()

    @contextmanager
    def _fake(*a, **k):
        yield tx

    monkeypatch.setattr(graph_query, "neo4j_session", _fake)
    monkeypatch.setattr(projection, "keys_for_document", lambda tx, doc_id, **kw: set())

    ing.retract_document("doc-1", "banking")

    assert ("MATCH (d:DocumentHistory {id: $doc_id}) REMOVE d.lifecycle_record, d.lifecycle_fingerprint",
            {"doc_id": "doc-1"}) in tx.calls


def test_the_lifecycle_kind_runs_first_and_re_applies_after_a_replay():
    kinds = curation_records.kinds()

    assert list(kinds)[0] == "lifecycle"
    assert kinds["lifecycle"].phase == "pre"
    assert kinds["lifecycle"].reapply_for(["doc-1", None]) == [lifecycle_records.record_id("doc-1")]


def test_remove_from_a_stub_record_finds_the_document_by_its_mark(no_rebuild):
    """`vault sync` hands a kind just `{"id": ...}` when the record's blob at
    base is unreadable: the document is found by the mark it carries."""
    rid = lifecycle_records.record_id("doc-1")

    ours = _Tx(holders=["doc-1"])
    assert lifecycle_records.remove(ours, {"id": rid}) == {("acme", "ORG", "banking")}
    lookups = [p for c, p in ours.calls if "RETURN d.id AS doc_id" in c]
    assert lookups == [{"id": rid}]
    assert ("MATCH (d) WHERE (d:Document OR d:DocumentHistory) AND d.id = $doc_id "
            "REMOVE d.lifecycle_record, d.lifecycle_fingerprint", {"doc_id": "doc-1"}) in ours.calls
    assert any("REMOVE d:DocumentHistory SET d:Document" in c for c in ours.texts())
    assert no_rebuild == []

    nobody = _Tx(holders=[])
    assert lifecycle_records.remove(nobody, {"id": rid}) == set()
    assert not any("SET d:Document" in c for c in nobody.texts()), "no document carries the mark: leave everything alone"


class _Model:
    """A one-document graph that models only what the fingerprint rule needs:
    labels, and a MARK that stamps only a node carrying `:DocumentHistory`."""

    def __init__(self, label):
        self.label = label          # "Document", "DocumentHistory" or None (absent)
        self.stamped = None
        self.calls = []

    def run(self, cypher, **params):
        self.calls.append((cypher, params))
        if "REMOVE d:Document SET d:DocumentHistory" in cypher and self.label == "Document":
            self.label = "DocumentHistory"
        if "SET d.lifecycle_record = $id" in cypher and "MATCH (d:DocumentHistory" in cypher and self.label == "DocumentHistory":
            self.stamped = params["fingerprint"]
        return _Result()


def test_apply_stamps_a_fingerprint_only_on_a_document_it_retired(no_rebuild):
    record = {"id": lifecycle_records.record_id("doc-1"), "doc_id": "doc-1", "domain": "banking", "status": "retired"}

    live = _Model("Document")
    lifecycle_records.apply(live, record, "fp")
    assert (live.label, live.stamped) == ("DocumentHistory", "fp"), "the mark follows the swap"

    absent = _Model(None)
    lifecycle_records.apply(absent, record, "fp")
    assert (absent.label, absent.stamped) == (None, None), "not in the graph yet: nothing stamped, so the fingerprint check cannot skip the record (a partial run does not advance the bookmark, so the next sync diffs it again)"


def test_kinds_registered_before_lifecycle_have_no_reapply_hook():
    kinds = curation_records.kinds()

    assert kinds["conflicts"].reapply_for is None
    assert kinds["supersessions"].reapply_for is None
    assert list(kinds)[:2] == ["lifecycle", "supersessions"]


class _RecordTx:
    """`.single()` returns what a real driver does: a `neo4j.Record`, which is
    a tuple with a mapping interface, not a `dict`."""

    def run(self, cypher, **params):
        import neo4j

        assert "RETURN d._domain AS domain" in cypher
        return _Result([neo4j.Record({"domain": "banking"})])


def test_the_domain_is_read_from_a_real_driver_record():
    import neo4j

    assert not isinstance(neo4j.Record({"domain": "banking"}), dict)

    record = lifecycle_records.record_for(_RecordTx(), "doc-1")

    assert record["domain"] == "banking"
    assert lifecycle_records.domains(record) == ["banking"]


def test_apply_twice_retires_once_and_re_marks_without_error(no_rebuild):
    """A second apply of the same record finds the document already in
    history: the label swap matches nothing, the mark is written again."""
    record = {"id": lifecycle_records.record_id("doc-1"), "doc_id": "doc-1", "domain": "banking", "status": "retired"}
    model = _Model("Document")

    first = lifecycle_records.apply(model, record, "fp")
    second = lifecycle_records.apply(model, record, "fp")

    assert first == second == set()
    assert (model.label, model.stamped) == ("DocumentHistory", "fp")
    swaps = [c for c, _ in model.calls if "REMOVE d:Document SET d:DocumentHistory" in c]
    assert len(swaps) == 2, "each apply sends the swap; only the first has a live node to match"
    marks = [p for c, p in model.calls if "SET d.lifecycle_record = $id" in c]
    assert marks == [{"doc_id": "doc-1", "id": record["id"], "fingerprint": "fp"}] * 2


def test_remove_of_an_already_restored_document_restores_nothing(no_rebuild):
    """The record no longer marks any document (a restore already ran): the
    guard finds nothing, so no label swap, no unmark and no fingerprint."""
    record = lifecycle_records.record_for(_Tx(), "doc-1")
    restored = _Tx(held=0)

    assert lifecycle_records.remove(restored, record) == set()
    assert lifecycle_records.remove(restored, record) == set()

    assert [c for c in restored.texts() if "SET d:Document" in c or "lifecycle_fingerprint" in c] == []
    assert len([1 for c in restored.texts() if "RETURN count(d) AS n" in c]) == 2
