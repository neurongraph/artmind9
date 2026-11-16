"""`ingest backfill-properties` (spec 2026-11-15 §4). Synthetic text only; the LLM is a stub."""
import json

import pytest

import artmind.ingest as ing
from artmind import backfill
from test_document_dates import JOURNAL_SCHEMA, _extract, _folder, _stub_llm, _vault

HEADER = JOURNAL_SCHEMA.split("entity_types:")[0]
SCHEMA_V1 = HEADER + """entity_types:
  EMOTION:
    kind: occurrent
    description: A feeling.
    properties:
      intensity: {}
  PERSON:
    kind: recurrent
    description: A person.
    properties:
      role: {}
"""
SCHEMA_V2 = SCHEMA_V1.replace("      intensity: {}\n", "      intensity: {}\n      trigger: {}\n")
SCHEMA_V3 = SCHEMA_V2.replace("      trigger: {}\n", "      trigger: {}\n      felt_on:\n        temporal: valid_from\n")


@pytest.fixture()
def staged(tmp_path, monkeypatch):
    doc = _vault(tmp_path, monkeypatch, "2026-01-07.md", "Felt uneasy before the review with Alex.\n")
    monkeypatch.setattr(ing, "LOGS_DIR", tmp_path / "logs")
    schema_file = ing.DOMAIN_SCHEMAS_DIR / "journal_schema.yaml"
    schema_file.write_text(SCHEMA_V1)
    _stub_llm(monkeypatch)

    def llm(step, model, prompt, debug_dir):
        if step.endswith("_entities"):
            return [{"id": "e1", "entity_class": "EMOTION", "name": "Unease"},
                    {"id": "e2", "entity_class": "PERSON", "name": "Alex"}], True
        if step.endswith("_properties"):
            return [{"id": "e1", "properties": {"intensity": "mild"}}, {"id": "e2", "properties": {"role": "lead"}}], True
        return [], True

    monkeypatch.setattr(ing, "_llm_extract", llm)
    _extract(doc)
    schema_file.write_text(SCHEMA_V2)  # trigger is declared later
    monkeypatch.setattr(ing, "commit_to_graph", lambda d, dom, defer_rebuild=False: True)
    monkeypatch.setattr(ing, "rebuild_projection", lambda domain: {})
    return _folder("2026-01-07")


def _answer(monkeypatch, answer, seen=None):
    def ask(step, model, prompt, debug_dir=None):
        if seen is not None:
            seen.append(prompt)
        return answer, True
    monkeypatch.setattr(backfill, "_ask", ask)


def _by_class(folder):
    return {o["entity_class"]: o for o in json.loads((folder / "observations.json").read_text())}


def test_fills_only_the_named_key_and_keeps_everything_else(staged, monkeypatch):
    seen = []
    _answer(monkeypatch, [{"id": "o1", "properties": {"trigger": "the review", "intensity": "IGNORED"}}], seen)
    before = _by_class(staged)

    report = backfill.backfill_domain("journal", targets={"EMOTION": ["trigger"]}, model="m")

    after = _by_class(staged)
    assert after["EMOTION"]["trigger"] == "the review"
    assert after["EMOTION"]["intensity"] == before["EMOTION"]["intensity"], "an unnamed key is never touched"
    assert after["PERSON"] == before["PERSON"]
    assert after["EMOTION"]["canonical_name"] == before["EMOTION"]["canonical_name"]
    blocks = seen[0].split("__list_of_entities__")[0]
    assert "For EMOTION, extract:" in blocks and "- trigger" in blocks and "intensity" not in blocks
    assert "PERSON" not in seen[0]
    assert report["committed"] == 1 and report["filled"] == 1


def test_an_undeclared_property_is_refused(staged):
    with pytest.raises(ValueError, match="mood not declared on EMOTION"):
        backfill.backfill_domain("journal", targets={"EMOTION": ["mood"]}, model="m")


def test_a_rerun_uses_the_cache(staged, monkeypatch):
    _answer(monkeypatch, [{"id": "o1", "properties": {"trigger": "the review"}}])
    backfill.backfill_domain("journal", targets={"EMOTION": ["trigger"]}, model="m")
    calls = []
    _answer(monkeypatch, [], calls)
    backfill.backfill_domain("journal", targets={"EMOTION": ["trigger"]}, model="m")
    assert calls == []


def test_the_folder_is_stashed_before_it_changes(staged, monkeypatch, tmp_path):
    original = (staged / "observations.json").read_bytes()
    _answer(monkeypatch, [{"id": "o1", "properties": {"trigger": "the review"}}])
    report = backfill.backfill_domain("journal", targets={"EMOTION": ["trigger"]}, model="m")
    (stashed,) = (tmp_path / "logs" / "backfill-stash").rglob("observations.json")
    assert stashed.read_bytes() == original and report["run_id"] in str(stashed)


def test_a_temporal_key_gets_the_anchor_and_redates(staged, monkeypatch):
    (ing.DOMAIN_SCHEMAS_DIR / "journal_schema.yaml").write_text(SCHEMA_V3)
    seen = []
    _answer(monkeypatch, [{"id": "o1", "properties": {"felt_on": "2026-01-06"}}], seen)
    backfill.backfill_domain("journal", targets={"EMOTION": ["felt_on"]}, model="m")
    emotion = _by_class(staged)["EMOTION"]
    assert "document dated 2026-01-07" in seen[0]
    assert (emotion["_valid_from"], emotion["_valid_time_source"]) == ("2026-01-06", "property")


def test_two_classes_share_one_call_per_chunk(staged, monkeypatch):
    seen = []
    _answer(monkeypatch, [], seen)
    backfill.backfill_domain("journal", targets={"EMOTION": ["trigger"], "PERSON": ["role"]}, model="m")
    assert len(seen) == 1 and "For EMOTION" in seen[0] and "For PERSON" in seen[0]


def test_the_chunk_cache_gets_the_filled_value_too(staged, monkeypatch):
    _answer(monkeypatch, [{"id": "o1", "properties": {"trigger": "the review"}}])
    backfill.backfill_domain("journal", targets={"EMOTION": ["trigger"]}, model="m")
    (chunk_file,) = (staged / "chunks").rglob("chunk_001.json")
    data = json.loads(chunk_file.read_text())
    ent = next(e for e in data["entities"] if e["entity_class"] == "EMOTION")
    entry = next(p for p in data["properties"] if p["id"] == ent["id"])
    assert entry["properties"]["trigger"] == "the review"


def test_a_table_folder_is_never_planned(staged, monkeypatch):
    doc = json.loads((staged / "document.json").read_text())
    (staged / "document.json").write_text(json.dumps({**doc, "source_kind": "table"}))
    calls = []
    _answer(monkeypatch, [], calls)
    report = backfill.backfill_domain("journal", targets={"EMOTION": ["trigger"]}, model="m")
    assert calls == [] and report["planned"] == [] and report["effect"]["changes"] == []


# ── review fixes: undo, failure handling, scope, concurrency ──────────────

def _record_commits(monkeypatch, fail=()):
    commits, rebuilds = [], []

    def commit(d, dom, defer_rebuild=False):
        commits.append((d.name, defer_rebuild))
        return d.name not in fail
    monkeypatch.setattr(ing, "commit_to_graph", commit)
    monkeypatch.setattr(ing, "rebuild_projection", lambda domain: rebuilds.append(domain) or {})
    return commits, rebuilds


def _cli(*args):
    from click.testing import CliRunner

    from artmind.cli import cli

    return CliRunner().invoke(cli, ["ingest", "backfill-properties", *args, "--compact"])


FILL = ["--domain", "journal", "--entityClass", "EMOTION", "--property", "trigger"]


def test_a_run_commits_the_folder_and_rebuilds_once(staged, monkeypatch):
    commits, rebuilds = _record_commits(monkeypatch)
    _answer(monkeypatch, [{"id": "o1", "properties": {"trigger": "the review"}}])
    backfill.backfill_domain("journal", targets={"EMOTION": ["trigger"]}, model="m")
    assert commits == [(staged.name, True)] and rebuilds == ["journal"]


def test_undo_restores_staging_and_the_chunk_cache_and_commits(staged, monkeypatch):
    commits, rebuilds = _record_commits(monkeypatch)
    (chunk_file,) = (staged / "chunks").rglob("chunk_001.json")
    obs_before, chunk_before = (staged / "observations.json").read_bytes(), chunk_file.read_bytes()
    _answer(monkeypatch, [{"id": "o1", "properties": {"trigger": "the review"}}])
    run = backfill.backfill_domain("journal", targets={"EMOTION": ["trigger"]}, model="m")
    assert (staged / "observations.json").read_bytes() != obs_before and chunk_file.read_bytes() != chunk_before
    assert "--undo RUN_ID" in run["effect"]["undo"]
    commits.clear(), rebuilds.clear()

    r = _cli("--undo", run["run_id"])

    assert r.exit_code == 0, r.output
    out = json.loads(r.stdout)
    assert out["restored"] == [staged.name] and out["committed"] == 1
    assert commits == [(staged.name, True)] and rebuilds == ["journal"]
    assert (staged / "observations.json").read_bytes() == obs_before
    assert chunk_file.read_bytes() == chunk_before


def test_undo_leaves_a_folder_written_again_since_the_run(staged, monkeypatch):
    commits, _ = _record_commits(monkeypatch)
    _answer(monkeypatch, [{"id": "o1", "properties": {"trigger": "the review"}}])
    run = backfill.backfill_domain("journal", targets={"EMOTION": ["trigger"]}, model="m")
    (staged / "observations.json").write_text("[]")
    commits.clear()
    out = backfill.undo_run(run["run_id"])
    assert out["restored"] == [] and commits == [] and "written again" in out["warnings"][0]
    assert (staged / "observations.json").read_text() == "[]"


def test_an_unknown_run_id_is_refused(staged):
    r = _cli("--undo", "../../etc")
    assert r.exit_code == 1 and "not a backfill run id" in r.output


def _three_folders(staged):
    import shutil

    names = [staged.name]
    for stem in ("2026-01-08~aaaaaaaaaaaa", "2026-01-09~bbbbbbbbbbbb"):
        shutil.copytree(staged, staged.parent / stem)
        names.append(stem)
    return names


def test_an_error_on_one_folder_does_not_stop_the_others(staged, monkeypatch):
    names = _three_folders(staged)
    commits, rebuilds = _record_commits(monkeypatch)
    _answer(monkeypatch, [{"id": "o1", "properties": {"trigger": "the review"}}])
    real_prompt = backfill._prompt

    def prompt(chunk, document, schema, scoped, folder):
        if folder.name == names[1]:
            raise RuntimeError("synthetic failure")
        return real_prompt(chunk, document, schema, scoped, folder)
    monkeypatch.setattr(backfill, "_prompt", prompt)

    report = backfill.backfill_domain("journal", targets={"EMOTION": ["trigger"]}, model="m")

    assert [c for c, _ in commits] == [names[0], names[2]] and rebuilds == ["journal"]
    assert report["errors"] == [names[1]] and report["committed"] == 2
    assert any("failed" in w for w in report["warnings"])


def test_a_raise_mid_run_still_rebuilds_what_committed(staged, monkeypatch):
    names = _three_folders(staged)
    commits, rebuilds = _record_commits(monkeypatch)
    _answer(monkeypatch, [{"id": "o1", "properties": {"trigger": "the review"}}])

    def commit(d, dom, defer_rebuild=False):
        if d.name == names[1]:
            raise KeyboardInterrupt
        commits.append((d.name, defer_rebuild))
        return True
    monkeypatch.setattr(ing, "commit_to_graph", commit)

    with pytest.raises(KeyboardInterrupt):
        backfill.backfill_domain("journal", targets={"EMOTION": ["trigger"]}, model="m")
    assert [c for c, _ in commits] == [names[0]] and rebuilds == ["journal"]


def test_nothing_committed_means_no_rebuild(staged, monkeypatch):
    commits, rebuilds = _record_commits(monkeypatch)
    _answer(monkeypatch, [])
    report = backfill.backfill_domain("journal", targets={"EMOTION": ["trigger"]}, model="m")
    assert commits == [] and rebuilds == [] and report["committed"] == 0


def test_refuses_while_the_worker_runs(staged, monkeypatch):
    monkeypatch.setattr(backfill, "_worker_running", lambda: True)
    monkeypatch.setattr(backfill, "_ask", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no LLM")))
    r = _cli(*FILL)
    assert r.exit_code == 1 and "worker is running" in r.output
    assert _cli(*FILL, "--dryRun").exit_code == 0, "a dry run writes nothing, so it may run"


def test_a_folder_written_since_planning_is_skipped(staged, monkeypatch):
    commits, _ = _record_commits(monkeypatch)

    def ask(step, model, prompt, debug_dir=None):
        doc = json.loads((staged / "document.json").read_text())
        (staged / "document.json").write_text(json.dumps({**doc, "title": "edited meanwhile"}))
        return [{"id": "o1", "properties": {"trigger": "the review"}}], True
    monkeypatch.setattr(backfill, "_ask", ask)
    before = (staged / "observations.json").read_bytes()

    report = backfill.backfill_domain("journal", targets={"EMOTION": ["trigger"]}, model="m")

    assert commits == [] and (staged / "observations.json").read_bytes() == before
    assert report["results"][0]["skipped"] and "changed since planning" in report["warnings"][0]


def test_a_failed_commit_is_recoverable_with_retry_commit(staged, monkeypatch):
    commits, _ = _record_commits(monkeypatch, fail={staged.name})
    _answer(monkeypatch, [{"id": "o1", "properties": {"trigger": "the review"}}])

    r = _cli(*FILL)

    assert r.exit_code == 1
    out = json.loads(r.stdout)
    assert out["next"] == f"artmind ingest backfill-properties --retryCommit {out['run_id']}"
    assert "--retryCommit" in r.output.split(r.stdout.strip())[-1], "the error text names the recovery too"
    commits2, rebuilds = _record_commits(monkeypatch)
    dry = json.loads(_cli("--retryCommit", out["run_id"], "--dryRun").stdout)
    assert dry["retried"] == [staged.name] and commits2 == []
    done = json.loads(_cli("--retryCommit", out["run_id"]).stdout)
    assert done["committed"] == 1 and commits2 == [(staged.name, True)] and rebuilds == ["journal"]
    assert json.loads(_cli("--retryCommit", out["run_id"]).stdout)["retried"] == [], "committed: nothing left"


def test_only_filled_observations_change_and_an_unfilled_folder_is_untouched(staged, monkeypatch):
    commits, _ = _record_commits(monkeypatch)
    obs = json.loads((staged / "observations.json").read_text())
    # PERSON carries a key its class does not declare (a retime would drop it).
    (staged / "observations.json").write_text(json.dumps(
        [{**o, "mood": "calm"} if o["entity_class"] == "PERSON" else o for o in obs], indent=2))
    before = (staged / "observations.json").read_bytes()

    _answer(monkeypatch, [])  # nothing filled
    backfill.backfill_domain("journal", targets={"EMOTION": ["trigger"]}, model="m")
    assert (staged / "observations.json").read_bytes() == before and commits == []

    _answer(monkeypatch, [{"id": "o1", "properties": {"trigger": "the review"}}], [])
    monkeypatch.setattr(backfill, "_cache_path", lambda prompt, model: staged.parent / "no-cache.json")
    backfill.backfill_domain("journal", targets={"EMOTION": ["trigger"]}, model="m")
    after = {o["entity_class"]: o for o in json.loads((staged / "observations.json").read_text())}
    assert after["PERSON"]["mood"] == "calm", "an unfilled observation is left exactly as it was"


def test_failed_chunks_warn_and_name_the_rerun(staged, monkeypatch):
    _record_commits(monkeypatch)
    monkeypatch.setattr(backfill, "_ask", lambda *a, **k: ([], False))
    out = json.loads(_cli(*FILL).stdout)
    assert len(out["failed_chunks"]) == 1
    assert any("1 chunk(s) failed; re-run to retry" in w for w in out["warnings"])
    assert out["next"] == "artmind ingest backfill-properties --domain journal --entityClass EMOTION --property trigger"


def test_a_step_name_per_chunk_and_a_debug_folder(staged, monkeypatch):
    seen = []
    monkeypatch.setattr(backfill, "_ask", lambda step, model, prompt, debug_dir=None:
                        seen.append((step, debug_dir)) or ([], True))
    backfill.backfill_domain("journal", targets={"EMOTION": ["trigger"]}, model="m")
    ((step, debug_dir),) = seen
    assert step == "backfill_001_properties" and debug_dir.is_dir() and staged.name in str(debug_dir)


def test_duplicate_answers_take_the_first_and_filled_counts_real_changes(staged, monkeypatch):
    commits, _ = _record_commits(monkeypatch)
    _answer(monkeypatch, [{"id": "o1", "properties": {"trigger": "first"}},
                          {"id": "o1", "properties": {"trigger": "second"}}])
    report = backfill.backfill_domain("journal", targets={"EMOTION": ["trigger"]}, model="m")
    assert _by_class(staged)["EMOTION"]["trigger"] == "first" and report["filled"] == 1

    commits.clear()
    _answer(monkeypatch, [{"id": "o1", "properties": {"trigger": "first"}}])
    monkeypatch.setattr(backfill, "_cache_path", lambda prompt, model: staged.parent / "no-cache.json")
    report = backfill.backfill_domain("journal", targets={"EMOTION": ["trigger"]}, model="m")
    assert report["filled"] == 0 and commits == [], "an unchanged value is no fill"


def test_an_empty_property_list_is_refused(staged):
    r = _cli("--domain", "journal", "--entityClass", "EMOTION", "--property", " , ")
    assert r.exit_code == 2 and "at least one --property" in r.output
    with pytest.raises(ValueError, match="at least one property"):
        backfill.backfill_domain("journal", targets={"EMOTION": []}, model="m")


# ── undo / retry recover every failure ────────────────────────────────────

def _filled_run(staged, monkeypatch):
    commits, rebuilds = _record_commits(monkeypatch)
    _answer(monkeypatch, [{"id": "o1", "properties": {"trigger": "the review"}}])
    run = backfill.backfill_domain("journal", targets={"EMOTION": ["trigger"]}, model="m")
    (chunk_file,) = (staged / "chunks").rglob("chunk_001.json")
    commits.clear(), rebuilds.clear()
    return run["run_id"], chunk_file, commits, rebuilds


def _stashed(run_id, staged, rel="observations.json"):
    return (ing.LOGS_DIR / "backfill-stash" / run_id / "journal" / staged.name / rel).read_bytes()


def test_a_partly_failed_undo_is_finished_by_a_second_undo(staged, monkeypatch):
    run_id, chunk_file, commits, _ = _filled_run(staged, monkeypatch)
    real_restore = backfill._restore_file
    monkeypatch.setattr(backfill, "_restore_file", lambda src, dst: (_ for _ in ()).throw(OSError("synthetic")))

    first = backfill.undo_run(run_id)

    assert first["restored"] == [] and commits == [] and "re-run the undo" in first["warnings"][0]
    assert not list(staged.rglob("*.undo.tmp"))
    monkeypatch.setattr(backfill, "_restore_file", real_restore)
    second = backfill.undo_run(run_id)
    assert second["restored"] == [staged.name] and commits == [(staged.name, True)]
    assert (staged / "observations.json").read_bytes() == _stashed(run_id, staged)
    assert chunk_file.read_bytes() == _stashed(run_id, staged, str(chunk_file.relative_to(staged)))


def test_an_undo_whose_observations_were_already_restored_still_commits(staged, monkeypatch):
    run_id, _, commits, _ = _filled_run(staged, monkeypatch)
    (staged / "observations.json").write_bytes(_stashed(run_id, staged))  # restored, never committed
    out = backfill.undo_run(run_id)
    assert out["restored"] == [staged.name] and commits == [(staged.name, True)]


def test_a_failed_undo_commit_is_recovered_by_retry_commit(staged, monkeypatch):
    run_id, _, _, _ = _filled_run(staged, monkeypatch)
    _record_commits(monkeypatch, fail={staged.name})
    r = _cli("--undo", run_id)
    assert r.exit_code == 1 and json.loads(r.stdout)["next"].endswith(f"--retryCommit {run_id}")

    commits, rebuilds = _record_commits(monkeypatch)
    out = backfill.retry_commits(run_id)
    assert out["retried"] == [staged.name] and commits == [(staged.name, True)] and rebuilds == ["journal"]
    assert (staged / "observations.json").read_bytes() == _stashed(run_id, staged), "the restored staging"
    assert backfill.retry_commits(run_id)["retried"] == [], "undo committed: nothing left"
    assert backfill.undo_run(run_id)["restored"] == [], "already undone and committed"


def test_a_folder_whose_write_failed_is_never_retried_or_undone(staged, monkeypatch):
    commits, _ = _record_commits(monkeypatch)
    _answer(monkeypatch, [{"id": "o1", "properties": {"trigger": "the review"}}])
    monkeypatch.setattr(ing, "write_staging", lambda *a, **k: (_ for _ in ()).throw(OSError("synthetic")))
    before = (staged / "observations.json").read_bytes()

    run = backfill.backfill_domain("journal", targets={"EMOTION": ["trigger"]}, model="m")

    assert commits == [] and run["errors"] == [staged.name]
    assert backfill.retry_commits(run["run_id"])["retried"] == []
    undo = backfill.undo_run(run["run_id"])
    assert undo["restored"] == [] and commits == []
    assert (staged / "observations.json").read_bytes() == before


def test_retry_commit_warns_when_staging_moved_but_commits_it(staged, monkeypatch):
    commits, _ = _record_commits(monkeypatch, fail={staged.name})
    _answer(monkeypatch, [{"id": "o1", "properties": {"trigger": "the review"}}])
    run_id = backfill.backfill_domain("journal", targets={"EMOTION": ["trigger"]}, model="m")["run_id"]
    (staged / "observations.json").write_text("[]")
    commits, _ = _record_commits(monkeypatch)
    out = backfill.retry_commits(run_id)
    assert commits == [(staged.name, True)] and "staging changed since the run" in out["warnings"][0]


def test_undo_with_fill_options_is_a_usage_error(staged):
    assert _cli("--undo", "0123456789ab", "--domain", "journal").exit_code == 2


def test_undo_restores_a_chunk_file_a_failed_patch_wrote_but_never_listed(staged, monkeypatch):
    commits, _ = _record_commits(monkeypatch)
    (chunk_file,) = (staged / "chunks").rglob("chunk_001.json")
    chunk_before = chunk_file.read_bytes()
    real_patch = backfill._patch_chunk_cache

    def patch_then_raise(folder, changed, values, stash):
        real_patch(folder, changed, values, stash)  # writes chunk_001.json
        raise OSError("synthetic, after the first file")
    monkeypatch.setattr(backfill, "_patch_chunk_cache", patch_then_raise)
    _answer(monkeypatch, [{"id": "o1", "properties": {"trigger": "the review"}}])

    run = backfill.backfill_domain("journal", targets={"EMOTION": ["trigger"]}, model="m")

    manifest = json.loads((ing.LOGS_DIR / "backfill-stash" / run["run_id"] / "manifest.json").read_text())
    assert manifest["folders"][staged.name]["chunk_files"] == [] and chunk_file.read_bytes() != chunk_before
    commits.clear()
    out = backfill.undo_run(run["run_id"])
    assert out["restored"] == [staged.name] and commits == [(staged.name, True)]
    assert chunk_file.read_bytes() == chunk_before
