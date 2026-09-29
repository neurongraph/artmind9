"""`artmind update retract`: a confirmed update leaves the graph, and its
staging folder is deleted so the retraction travels (spec 2026-09-26 §7).
The graph seams are recorded, never a live Neo4j (CLAUDE.md §3)."""
import json

import pytest
from click.testing import CliRunner

import artmind.update as upd


@pytest.fixture()
def kg_dir(tmp_path, monkeypatch):
    import paths

    target = tmp_path / "kg"
    monkeypatch.setattr(paths, "KG_DIR", target)
    return target


def _folder(kg_dir, draft_id, domain="general"):
    from artmind.ingest import write_staging

    folder = kg_dir / domain / f"update__s1__{draft_id}"
    write_staging(folder, {"document.json": {"id": f"update:s1:{draft_id}", "source_kind": "user_chat"},
                           "observations.json": []})
    return folder


@pytest.fixture()
def seams(monkeypatch):
    import artmind.ingest as ing
    import artmind.same_as as same_as
    import artmind.table2graph as t2g

    seen = {"retract": [], "rebuild": [], "drafts": [], "sessions": []}
    monkeypatch.setattr(upd, "_get_update_session", lambda sid: {"session_id": sid, "domain": "general"} if sid == "s1" else None)
    monkeypatch.setattr(ing, "retract_document", lambda doc_id, domain: seen["retract"].append((doc_id, domain)) or {
        "affected_keys": [["alice", "PERSON", "general"]],
    })
    monkeypatch.setattr(same_as, "load_groups", lambda: [[("alice", "PERSON", "general"), ("a. smith", "PERSON", "general")]])
    monkeypatch.setattr(t2g, "_rebuild_in_batches", lambda keys, groups=None: seen["rebuild"].append((sorted(keys), groups)) or {"keys": len(keys)})
    monkeypatch.setattr(ing, "_sweep_embeddings", lambda domain, keys: 0)
    monkeypatch.setattr(upd, "_update_draft_status", lambda draft_id, status: seen["drafts"].append((draft_id, status)))
    monkeypatch.setattr(upd, "_update_session_status", lambda sid, status: seen["sessions"].append((sid, status)))
    return seen


def test_retract_update_retracts_the_graph_then_deletes_the_folder(kg_dir, seams):
    folder = _folder(kg_dir, 7)

    result = upd.retract_update("s1")

    assert seams["retract"] == [("update:s1:7", "general")]
    group = [("alice", "PERSON", "general"), ("a. smith", "PERSON", "general")]
    assert seams["rebuild"] == [(sorted(group), [group])], "the whole same-as group is rebuilt"
    assert not folder.exists()
    assert seams["drafts"] == [(7, "retracted")]
    assert seams["sessions"] == [("s1", "retracted")]
    assert result["retracted"] == ["update:s1:7"]


def test_retract_one_draft_leaves_the_sessions_other_updates(kg_dir, seams):
    _folder(kg_dir, 7)
    keep = _folder(kg_dir, 8)

    upd.retract_update("s1", draft_id=7)

    assert seams["retract"] == [("update:s1:7", "general")]
    assert keep.exists()
    assert seams["sessions"] == []


def test_nothing_to_retract_is_an_error(kg_dir, seams):
    with pytest.raises(ValueError, match="No confirmed update to retract"):
        upd.retract_update("s1")
    with pytest.raises(ValueError, match="No such update session"):
        upd.retract_update("nope")


def test_a_failed_graph_retraction_keeps_the_folder_for_a_retry(kg_dir, seams, monkeypatch):
    import artmind.ingest as ing

    folder = _folder(kg_dir, 7)

    def _boom(doc_id, domain):
        raise RuntimeError("neo4j down")

    monkeypatch.setattr(ing, "retract_document", _boom)

    with pytest.raises(RuntimeError, match="neo4j down"):
        upd.retract_update("s1")
    assert folder.is_dir()


def test_retract_update_with_no_same_as_groups_still_rebuilds(kg_dir, seams, monkeypatch):
    import artmind.same_as as same_as

    monkeypatch.setattr(same_as, "load_groups", lambda: [])
    folder = _folder(kg_dir, 7)

    result = upd.retract_update("s1")

    assert seams["rebuild"] == [([("alice", "PERSON", "general")], [])]
    assert not folder.exists()
    assert result["retracted"] == ["update:s1:7"]


def test_the_cli_retracts_and_prints_json(monkeypatch):
    from artmind.cli import cli

    seen = []
    monkeypatch.setattr(upd, "retract_update", lambda session, draft_id: seen.append((session, draft_id)) or {"retracted": ["update:s1:7"]})

    result = CliRunner().invoke(cli, ["update", "retract", "--session", "s1", "--draft", "7", "--compact"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == {"retracted": ["update:s1:7"]}
    assert seen == [("s1", 7)]


def test_the_cli_retracts_the_whole_session_without_draft(monkeypatch):
    from artmind.cli import cli

    seen = []
    monkeypatch.setattr(upd, "retract_update", lambda session, draft_id: seen.append((session, draft_id)) or {"retracted": ["update:s1:7", "update:s1:8"]})

    result = CliRunner().invoke(cli, ["update", "retract", "--session", "s1", "--compact"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == {"retracted": ["update:s1:7", "update:s1:8"]}
    assert seen == [("s1", None)]


def test_the_cli_reports_a_missing_update(monkeypatch):
    from artmind.cli import cli

    def _missing(session, draft_id):
        raise ValueError("No confirmed update to retract for session 's9'")

    monkeypatch.setattr(upd, "retract_update", _missing)

    result = CliRunner().invoke(cli, ["update", "retract", "--session", "s9"])

    assert result.exit_code != 0
    assert "No confirmed update to retract" in result.output
