"""Step `rekey` (plan Task 35; spec 2026-11-15 §5.2, §8; questions 12-14).
Synthetic keys and invented names only; a recording graph, never Neo4j."""
import json
from collections import Counter
from contextlib import contextmanager

import pytest
import yaml

from _dry_run import WritableRecorder, assert_no_write_cypher
from _neo4j_recorder import Recorder
from artmind import curation_records, ingest, same_as
from artmind.migrations.temporal_v1 import rekey
from artmind.migrations.temporal_v1 import steps as steps_mod
from artmind.migrations.temporal_v1.steps import MigrationContext
from artmind.observations import entity_id

SCHEMA = {"entity_types": {"ACTIVITY": {"kind": "occurrent", "identity": "occasion"},
                           "EMOTION": {"kind": "recurrent", "identity": "category"},
                           "PERSON": {"kind": "recurrent", "identity": "name"}}}
T30, T02, TM30 = "tennis~2024-03-30|ACTIVITY|pj", "tennis~2024-04-02|ACTIVITY|pj", "tennis match~2024-03-30|ACTIVITY|pj"
MOVES = {"tennis|ACTIVITY|pj": {T30, T02}, "tennis match|ACTIVITY|pj": {TM30}}


def schema_for(domain):
    return SCHEMA


# ── pure parts ────────────────────────────────────────────────────────────────

def test_key_moves_compares_by_observation_id():
    before = [{"id": "o1", "key": "tennis|ACTIVITY|pj"}, {"id": "o2", "key": "alex|PERSON|pj"}]
    after = [{"id": "o1", "key": T30}, {"id": "o2", "key": "alex|PERSON|pj"}]
    assert rekey.key_moves(before, after) == {"tennis|ACTIVITY|pj": {T30}}


def test_key_moves_keeps_an_unmoved_observation_of_a_moved_key():
    before = [{"id": "o1", "key": "tennis|ACTIVITY|pj"}, {"id": "o2", "key": "tennis|ACTIVITY|pj"}]
    after = [{"id": "o1", "key": T30}, {"id": "o2", "key": "tennis|ACTIVITY|pj"}]
    assert rekey.key_moves(before, after) == {"tennis|ACTIVITY|pj": {T30, "tennis|ACTIVITY|pj"}}


def _groups(path, groups):
    path.write_text(yaml.safe_dump({"groups": groups}, sort_keys=False))


def test_same_as_groups_split_per_shared_day(tmp_path):
    path = tmp_path / "same_as.yaml"
    _groups(path, [
        {"canonical": "tennis|ACTIVITY|pj", "members": ["tennis|ACTIVITY|pj", "tennis match|ACTIVITY|pj"]},
        {"canonical": "calm|EMOTION|pj", "members": ["calm|EMOTION|pj", "serenity|EMOTION|pj"]},
        {"canonical": "alex|PERSON|pj", "members": ["alex|PERSON|pj", "alexander|PERSON|pj"]}])
    groups, report = rekey.migrate_same_as(path, MOVES, schema_for, write=True)
    assert groups == [  # file order kept
        {"canonical": T30, "members": [T30, TM30]},
        {"canonical": "alex|PERSON|pj", "members": ["alex|PERSON|pj", "alexander|PERSON|pj"]}]
    assert report == {"groups_before": 3, "groups_after": 2, "rekeyed": 1, "split": 0, "emptied": 0,
                      "dropped_category": 1, "members_dropped": 1}
    assert same_as.load_groups(path)[0] == [tuple(T30.split("|")), tuple(TM30.split("|"))]


def test_same_as_dry_run_writes_no_file_and_other_domains_are_left(tmp_path):
    path = tmp_path / "same_as.yaml"
    _groups(path, [{"canonical": "tennis|ACTIVITY|pj", "members": ["tennis|ACTIVITY|pj", "tennis match|ACTIVITY|pj"]}])
    before = path.read_bytes()
    rekey.migrate_same_as(path, MOVES, schema_for, write=False)
    assert path.read_bytes() == before
    rekey.migrate_same_as(path, MOVES, schema_for, write=True, domain="other")
    assert path.read_bytes() == before


def test_open_proposals_across_days_are_closed_with_the_reason():
    open_rows = [{"id": "p1", "canonical": T30, "members": [T30, T02]},
                 {"id": "p2", "canonical": "calm|EMOTION|pj", "members": ["calm|EMOTION|pj", "serenity|EMOTION|pj"]},
                 {"id": "p3", "canonical": "alex|PERSON|pj", "members": ["alex|PERSON|pj", "al|PERSON|pj"]}]
    rec = Recorder(lambda c, p: open_rows if c.startswith("MATCH (p:SameAsProposal)") else [])
    closed = rekey.close_proposals(rec, MOVES, schema_for, dry_run=False)
    (cypher, params), = rec.queries_containing("SET p.status = 'rejected'")
    assert params["rows"] == [{"id": "p1", "reason": "identity:occasion"}, {"id": "p2", "reason": "identity:category"}]
    assert closed == 2


def test_an_old_proposal_under_an_old_key_with_several_days_closes():
    rows = [{"id": "p1", "canonical": "tennis|ACTIVITY|pj", "members": ["tennis|ACTIVITY|pj", "tennis match|ACTIVITY|pj"]}]
    rec = Recorder(lambda c, p: rows if c.startswith("MATCH (p:SameAsProposal)") else [])
    assert rekey.close_proposals(rec, MOVES, schema_for, dry_run=False) == 1
    (_, params), = rec.queries_containing("SET p.status = 'rejected'")
    assert params["rows"] == [{"id": "p1", "reason": "identity:occasion"}]
    # Without the moves it is a same-day (undated) pair: nothing to close.
    assert rekey.refused_proposals(Recorder(lambda c, p: rows), {}, schema_for) == []


def test_proposals_dry_run_closes_nothing():
    rec = Recorder(lambda c, p: [{"id": "p2", "canonical": "calm|EMOTION|pj", "members": ["calm|EMOTION|pj", "x|EMOTION|pj"]}])
    assert rekey.close_proposals(rec, MOVES, schema_for, dry_run=True) == 1
    assert not rec.queries_containing("SET p.status")


@pytest.fixture
def curation(tmp_path, monkeypatch):
    import paths

    monkeypatch.setattr(paths, "CURATION_DIR", tmp_path / "curation")
    return tmp_path / "curation"


def _record(kind, record):
    curation_records.write_record(kind, record)
    return curation_records.record_path(kind, record["id"])


def test_records_follow_the_moved_keys(curation):
    from artmind.mart import records as mart_records

    old_syn = _record("syntheses", {"id": entity_id(("tennis", "ACTIVITY", "pj")), "key": "tennis|ACTIVITY|pj",
                                    "description": "Played tennis."})
    old_dim = _record("mart_dimension", {"id": mart_records._id("pj", "place", "tennis|ACTIVITY|pj", "rule"),
                                         "domain": "pj", "dimension": "place", "member_key": "tennis|ACTIVITY|pj",
                                         "levels": {"city": "Testville"}, "source": "rule"})
    conflict = _record("conflicts", {"id": "c1", "status": "open", "domains": ["pj"],
                                     "entities": [{"side": "a", "key": "tennis|ACTIVITY|pj"},
                                                  {"side": "b", "key": "tennis match|ACTIVITY|pj"}]})
    untouched = _record("conflicts", {"id": "c2", "status": "open", "domains": ["pj"],
                                      "entities": [{"side": "a", "key": "alex|PERSON|pj"}]})
    changes, report = rekey.migrate_records(MOVES, Counter({T30: 1, T02: 3}), write=True)
    assert report == {"syntheses": 1, "mart_dimension": 1, "conflicts": 1}
    assert not old_syn.exists() and not old_dim.exists()
    moved = json.loads(curation_records.record_path("syntheses", entity_id(tuple(T02.split("|")))).read_text())
    assert moved["key"] == T02 and moved["description"] == "Played tennis."
    dims = sorted(json.loads(p.read_text())["member_key"] for p in (curation / "mart_dimension").glob("*.json"))
    assert dims == [T30, T02]
    closed = json.loads(conflict.read_text())
    assert (closed["status"], closed["resolution_reason"]) == ("dismissed", "identity:occasion")
    assert json.loads(untouched.read_text())["status"] == "open"


def test_records_dry_run_writes_nothing(curation):
    path = _record("syntheses", {"id": entity_id(("tennis", "ACTIVITY", "pj")), "key": "tennis|ACTIVITY|pj"})
    changes, _ = rekey.migrate_records(MOVES, Counter(), write=False)
    assert len(changes) == 1 and path.exists() and len(list((curation / "syntheses").iterdir())) == 1


# ── the step over a synthetic vault ───────────────────────────────────────────

SCHEMA_YAML = """name: pj
entity_types:
  ACTIVITY:
    kind: occurrent
    identity: occasion
    description: One occasion.
  OBJECT:
    kind: recurrent
    identity: name
    description: A thing.
    properties:
      type: {}
  EMOTION:
    kind: recurrent
    identity: category
    description: A feeling.
  PERSON:
    kind: recurrent
    identity: name
    description: A person.
"""


def _obs(oid, doc, name, cls, **extra):
    return {"id": oid, "doc_id": doc, "key": f"{name.lower()}|{cls}|pj", "entity_class": cls, "_domain": "pj",
            "canonical_name": name, "name": name, **extra}


class Graph:
    """This machine's graph: observation keys by document, open proposals, and the writes."""

    def __init__(self):
        self.keys: dict[str, dict[str, str]] = {}
        self.proposals: list[dict] = []
        self.commits: list[str] = []
        self.rebuilds: list[str] = []
        self.fail_commit = False

    def respond(self, cypher, params):
        if "UNWIND $ids AS doc_id" in cypher:
            return [{"id": i, "key": k} for d in params["ids"] for i, k in self.keys.get(d, {}).items()]
        if cypher.startswith("MATCH (p:SameAsProposal)"):
            return [p for p in self.proposals if p["status"] == "open"]
        if "SET p.status = 'rejected'" in cypher:
            ids = {r["id"] for r in params["rows"]}
            for p in self.proposals:
                if p["id"] in ids:
                    p["status"] = "rejected"
        return []


@pytest.fixture
def vault(tmp_path, monkeypatch, curation):
    import paths

    schemas = tmp_path / "schemas"
    schemas.mkdir()
    (schemas / "pj_schema.yaml").write_text(SCHEMA_YAML)
    monkeypatch.setattr(ingest, "DOMAIN_SCHEMAS_DIR", schemas)
    monkeypatch.setattr(ingest, "KG_DIR", tmp_path / "kg")
    monkeypatch.setattr(ingest, "LOGS_DIR", tmp_path / "logs")
    monkeypatch.setattr(same_as, "SAME_AS_PATH", tmp_path / "same_as.yaml")
    monkeypatch.setattr(paths, "ARTMIND_VAULT_DIR", str(tmp_path))
    monkeypatch.setattr(rekey, "schema_for", lambda d: yaml.safe_load(SCHEMA_YAML))
    monkeypatch.setattr("artmind.migrations.temporal_v1.cli._all_domains", lambda: ["pj", "general"])
    graph = Graph()
    for folder, doc, day, observations in [
        ("d1", "doc1", "2024-03-30", [_obs("o1", "doc1", "Tennis", "ACTIVITY"),
                                      _obs("o2", "doc1", "Tennis match", "ACTIVITY"),
                                      _obs("o3", "doc1", "Flight to Lisbon", "OBJECT", type="travel"),
                                      _obs("o4", "doc1", "Alex", "PERSON")]),
        ("d2", "doc2", "2024-04-02", [_obs("o5", "doc2", "Tennis", "ACTIVITY")])]:
        path = tmp_path / "kg" / "pj" / folder
        path.mkdir(parents=True)
        (path / "document.json").write_text(json.dumps({"id": doc, "name": f"{folder}.md", "_valid_from": day,
                                                        "_valid_time_source": "filename", "_valid_precision": "day"}))
        (path / "observations.json").write_text(json.dumps(observations, indent=2))
        graph.keys[doc] = {o["id"]: o["key"] for o in observations}
    _groups(tmp_path / "same_as.yaml", [
        {"canonical": "tennis|ACTIVITY|pj", "members": ["tennis|ACTIVITY|pj", "tennis match|ACTIVITY|pj"]}])
    graph.proposals = [{"id": "p1", "status": "open", "canonical": "tennis|ACTIVITY|pj",
                        "members": ["tennis|ACTIVITY|pj", "tennis match|ACTIVITY|pj"]}]
    _record("syntheses", {"id": entity_id(("tennis", "ACTIVITY", "pj")), "key": "tennis|ACTIVITY|pj",
                          "description": "Tennis."})

    def commit(folder, domain, defer_rebuild=False):
        assert defer_rebuild is True
        if graph.fail_commit:
            return False
        document = json.loads((folder / "document.json").read_text())
        graph.keys[document["id"]] = {o["id"]: o["key"] for o in json.loads((folder / "observations.json").read_text())}
        graph.commits.append(folder.name)
        return True

    monkeypatch.setattr(ingest, "commit_to_graph", commit)
    monkeypatch.setattr(ingest, "rebuild_projection", lambda domain: graph.rebuilds.append(domain) or {})
    recorder = WritableRecorder(graph.respond)

    @contextmanager
    def session():
        yield recorder

    graph.recorder, graph.session, graph.root = recorder, session, tmp_path
    return graph


def _ctx(vault, domains=None, dry_run=False):
    return MigrationContext(domains=domains, session_factory=vault.session, dry_run=dry_run)


def _files(root):
    return {p.relative_to(root): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def test_the_step_is_registered_last_and_travels():
    step = steps_mod.STEPS[-1]
    assert step.name == "rekey" and step.travels and step.effect is not None


def test_pending_counts_every_part(vault):
    counts = rekey.pending(_ctx(vault))
    assert {k: counts[k] for k in rekey.COUNT_KEYS if k in counts} == {
        "objects_reclassified": 1, "entity_rekey": 3, "same_as_groups": 1, "sameas_proposals_closed": 1,
        "curation_records": 1}
    assert counts["classes"] == ["ACTIVITY", "OBJECT"]
    assert counts["by_domain"].keys() == {"pj"}
    assert "same_as.yaml" in counts["files"] and "kg/pj" in counts["files"]


def test_dry_run_writes_no_file_and_sends_no_write_cypher(vault):
    before = _files(vault.root)
    rekey.pending(_ctx(vault, dry_run=True))
    assert rekey.apply(_ctx(vault, dry_run=True), True) == {}
    assert _files(vault.root) == before
    assert_no_write_cypher(vault.recorder)


def test_apply_rekeys_writes_records_commits_once_and_converges(vault):
    applied = rekey.apply(_ctx(vault), False)
    assert applied["objects_reclassified"] == 1 and applied["documents_committed"] == 2
    staged = json.loads((vault.root / "kg/pj/d1/observations.json").read_text())
    assert {o["id"]: (o["key"], o["entity_class"]) for o in staged}["o3"] == (
        "flight to lisbon~2024-03-30|ACTIVITY|pj", "ACTIVITY")
    assert "type" not in staged[2]
    assert vault.keys["doc2"] == {"o5": T02}
    assert yaml.safe_load((vault.root / "same_as.yaml").read_text())["groups"] == [
        {"canonical": T30, "members": [T30, TM30]}]
    assert vault.proposals[0]["status"] == "rejected"
    assert vault.rebuilds == ["pj"]
    # the synthesis moved to the new entity on the graph too
    assert vault.recorder.queries_containing("DETACH DELETE s")
    assert rekey.pending(_ctx(vault)) == {}
    # recovery copies of every file the run wrote
    copies = list((vault.root / "logs/migrate-temporal").glob("*/pj/files/*"))
    assert len(copies) >= 4


def test_a_failure_in_the_records_restores_every_file_and_commits_nothing(vault, monkeypatch):
    before = _files(vault.root)
    monkeypatch.setattr(rekey, "write_records", lambda changes: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError):
        rekey.apply(_ctx(vault), False)
    after = {k: v for k, v in _files(vault.root).items() if not str(k).startswith("logs")}
    assert after == before
    assert vault.commits == [] and vault.rebuilds == []
    assert not vault.recorder.queries_containing("SET p.status")


def test_a_failed_commit_leaves_staging_and_a_rerun_resumes(vault):
    vault.fail_commit = True
    with pytest.raises(RuntimeError, match="run `artmind migrate temporal` again"):
        rekey.apply(_ctx(vault), False)
    left = rekey.pending(_ctx(vault))
    assert left["documents_to_commit"] == 2 and "objects_reclassified" not in left
    # the run's own written files do not block the resume: nothing left to write
    assert rekey.writes(_ctx(vault)) == []
    vault.fail_commit = False
    rekey.apply(_ctx(vault), False)
    assert rekey.pending(_ctx(vault)) == {}
    assert vault.keys["doc2"] == {"o5": T02}


@pytest.mark.parametrize("stage", ["staging", "same_as", "records", "graph"])
def test_a_kill_between_stages_converges_on_rerun(vault, stage):
    """A kill (nothing restores) inside or after a stage: the re-run reads the old keys
    from the graph, is not blocked by the run's own files, and finishes the job."""
    class Killed(BaseException):
        pass

    def boom(*a, **k):
        raise Killed()

    real_staging = ingest.write_staging
    with pytest.MonkeyPatch.context() as m:
        m.setattr(rekey.Stash, "restore", lambda self: None)
        if stage == "staging":  # the first folder written, the second not
            calls = []
            m.setattr(ingest, "write_staging",
                      lambda *a, **k: boom() if calls else (calls.append(1), real_staging(*a, **k)))
        elif stage == "same_as":
            m.setattr(rekey, "write_records", boom)
        elif stage == "records":  # the new record written, the old one not removed
            m.setattr(curation_records, "delete_record", boom)
        else:
            m.setattr(rekey, "_graph_stage", boom)
        with pytest.raises(Killed):
            rekey.apply(_ctx(vault), False)
    assert rekey.pending(_ctx(vault))
    assert set(rekey.own_work(_ctx(vault))) <= set(rekey.writes(_ctx(vault)))
    rekey.apply(_ctx(vault), False)
    assert rekey.pending(_ctx(vault)) == {}
    assert yaml.safe_load((vault.root / "same_as.yaml").read_text())["groups"] == [
        {"canonical": T30, "members": [T30, TM30]}]
    assert vault.proposals[0]["status"] == "rejected"
    assert vault.keys["doc1"]["o3"] == "flight to lisbon~2024-03-30|ACTIVITY|pj"
    assert not curation_records.record_path("syntheses", entity_id(("tennis", "ACTIVITY", "pj"))).exists()
    assert curation_records.record_path("syntheses", entity_id(tuple(T30.split("|")))).exists() or \
        curation_records.record_path("syntheses", entity_id(tuple(T02.split("|")))).exists()


def test_a_second_run_after_apply_is_a_no_op(vault):
    rekey.apply(_ctx(vault), False)
    before, commits = _files(vault.root), list(vault.commits)
    assert rekey.pending(_ctx(vault)) == {}
    assert rekey.apply(_ctx(vault), False) == {}
    assert {k: v for k, v in _files(vault.root).items() if not str(k).startswith("logs")} == \
        {k: v for k, v in before.items() if not str(k).startswith("logs")}
    assert vault.commits == commits


def test_domain_scoping(vault):
    assert rekey.pending(_ctx(vault, domains=["other"])) == {}
    assert rekey.pending(_ctx(vault, domains=["pj"]))["entity_rekey"] == 3
    before = _files(vault.root)
    assert rekey.apply(_ctx(vault, domains=["other"]), False) == {}
    assert _files(vault.root) == before


def test_effect_names_the_kinds_classes_and_files(vault):
    part = rekey.effect(rekey.pending(_ctx(vault)))
    kinds = {c.kind: c for c in part["changes"]}
    assert set(kinds) == {"objects_reclassified", "entity_rekey", "same_as_groups", "sameas_proposals_closed",
                          "curation_records"}
    assert kinds["entity_rekey"].sample == ["ACTIVITY", "OBJECT"]
    assert any(r.startswith("curation/syntheses/") for r in part["records"])
    assert "git restore" in part["undo"] and "projection rebuild" in part["undo"]


# ── update-session folders, resumed record moves, pulled conflicts, memo ──────

def _chat_folder(vault, observations):
    path = vault.root / "kg" / "pj" / "chat1"
    path.mkdir(parents=True)
    (path / "document.json").write_text(json.dumps({"id": "chat1", "name": "chat1", "source_kind": "user_chat",
                                                    "_valid_from": "2024-05-01", "_valid_time_source": "user_chat"}))
    (path / "observations.json").write_text(json.dumps(observations, indent=2))
    vault.keys["chat1"] = {o["id"]: o["key"] for o in observations}
    return path


def test_a_chat_link_keeps_its_picked_key_and_dates(vault):
    linked = {"id": "c1", "doc_id": "chat1", "key": "alexander the tester|PERSON|pj", "entity_class": "PERSON",
              "_domain": "pj", "canonical_name": "Alex", "_valid_from": "2024-05-01",
              "_valid_time_source": "user_chat", "_valid_precision": "day"}
    dated = {"id": "c2", "doc_id": "chat1", "key": T30, "entity_class": "ACTIVITY", "_domain": "pj",
             "canonical_name": "Tennis on 2024-03-30", "_valid_from": "2024-03-30", "_valid_precision": "day"}
    path = _chat_folder(vault, [linked, dated])
    before = path.joinpath("observations.json").read_bytes()
    rekey.apply(_ctx(vault), False)
    assert path.joinpath("observations.json").read_bytes() == before
    assert "chat1" not in vault.commits


def test_an_old_chat_occasion_without_a_day_gains_only_its_day(vault):
    old = {"id": "c3", "doc_id": "chat1", "key": "padel night|ACTIVITY|pj", "entity_class": "ACTIVITY",
           "_domain": "pj", "canonical_name": "Padel night on Friday", "name": "padel", "_valid_from": "2024-05-03",
           "_valid_time_source": "user_chat", "_valid_precision": "day", "note": "kept"}
    undated = {**old, "id": "c4", "key": "chess|ACTIVITY|pj", "_valid_precision": "none"}
    path = _chat_folder(vault, [old, undated])
    rekey.apply(_ctx(vault), False)
    written = json.loads(path.joinpath("observations.json").read_text())
    assert written[0] == {**old, "key": "padel night~2024-05-03|ACTIVITY|pj"}
    assert written[1] == undated
    assert vault.keys["chat1"]["c3"] == "padel night~2024-05-03|ACTIVITY|pj"


def test_a_resumed_run_that_only_moves_a_synthesis_rebuilds(vault, monkeypatch):
    with pytest.MonkeyPatch.context() as m:
        m.setattr(rekey, "_graph_stage", lambda ctx, plan: (_ for _ in ()).throw(KeyboardInterrupt()))
        with pytest.raises(KeyboardInterrupt):
            rekey.apply(_ctx(vault), False)
    # another process committed the staging meanwhile; the record move is still unapplied here
    for doc, folder in (("doc1", "d1"), ("doc2", "d2")):
        vault.keys[doc] = {o["id"]: o["key"] for o in
                           json.loads((vault.root / "kg/pj" / folder / "observations.json").read_text())}
    left = rekey.pending(_ctx(vault))
    assert left.get("records_to_apply") == 1 and "documents_to_commit" not in left
    rekey.apply(_ctx(vault), False)
    assert vault.commits == [] and vault.rebuilds == ["pj"]
    assert rekey.pending(_ctx(vault)) == {}


def test_a_pulled_dismissed_conflict_is_applied_before_the_commits(vault):
    record = {"id": "c9", "status": "dismissed", "resolution_reason": "identity:occasion", "domains": ["pj"],
              "entities": [{"side": "a", "key": "tennis|ACTIVITY|pj", "id": "x"},
                           {"side": "b", "key": "tennis match|ACTIVITY|pj", "id": "y"}]}
    _record("conflicts", record)
    assert rekey.pending(_ctx(vault))["records_to_apply"] == 1
    rekey.apply(_ctx(vault), False)
    applied = [p for c, p in vault.recorder.calls if "MERGE (co:Conflict" in c]
    assert [p["id"] for p in applied] == ["c9"] and applied[0]["status"] == "dismissed"


def test_plans_are_memoised_until_apply(vault, monkeypatch):
    calls = []
    real = rekey._plan_domain
    monkeypatch.setattr(rekey, "_plan_domain", lambda d, s=None: calls.append(d) or real(d, s))
    rekey.pending(_ctx(vault))
    rekey.writes(_ctx(vault))
    rekey.own_work(_ctx(vault))
    assert calls.count("pj") == 1
    rekey.apply(_ctx(vault), False)
    rekey.pending(_ctx(vault))
    assert calls.count("pj") == 2
