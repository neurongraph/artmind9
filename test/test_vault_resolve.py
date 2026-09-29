"""`artmind vault resolve` (spec 2026-09-26 §5 R7) against real merge
conflicts in throwaway git repos: a whole side per unit, deterministic in
either merge direction, only under `.artmind/data/`, never a commit."""
import json
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from artmind import vault, vault_resolve as vr
from artmind.document_identity import compute_content_sha256

KG = ".artmind/data/kg/general/policy"
NOTE = "notes/policy.md"


def _git(repo, *args, check=True):
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=check).stdout


def _write(repo, files: dict):
    for rel, content in files.items():
        path = repo / rel
        if content is None:
            path.unlink(missing_ok=True)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content.encode() if isinstance(content, str) else content)


def _commit(repo, message):
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", message)


def _init_repo(root: Path) -> Path:
    root.mkdir(parents=True)
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "T")
    vault.write_gitattributes(root)  # .artmind/data/** merge=binary (R3)
    return root


@pytest.fixture()
def repo(tmp_path, monkeypatch):
    import paths

    monkeypatch.setattr(paths, "WORKER_PID_FILE", tmp_path / "worker.pid")
    return _init_repo(tmp_path / "v")


def _conflict(repo, base: dict, ours: dict, theirs: dict, *, into="main", other="theirs"):
    """Commit `base` on main, `theirs` on a branch and `ours` on main, then
    merge the branch into `into` -- leaving the merge in progress."""
    _write(repo, base)
    _commit(repo, "base")
    _git(repo, "checkout", "-qb", "theirs")
    _write(repo, theirs)
    _commit(repo, "theirs")
    _git(repo, "checkout", "-q", "main")
    _write(repo, ours)
    _commit(repo, "ours")
    if into != "main":
        _git(repo, "checkout", "-q", into)
    _git(repo, "merge", "--no-edit", other, check=False)
    assert (repo / ".git" / "MERGE_HEAD").exists()


def _doc_folder(doc_id="id-1", *, body="Body v1.\n", obs="[1]", rel="[]", extra=None):
    document = {"id": doc_id, "version": 2, "content_sha256": compute_content_sha256(body),
                "source_path": NOTE, "source_type": "md"}
    files = {
        f"{KG}/document.json": json.dumps(document, indent=2),
        f"{KG}/observations.json": obs,
        f"{KG}/chunks.json": "[]",
        f"{KG}/relationships.json": rel,
    }
    files.update(extra or {})
    return files


def _note(body):
    return f"---\n_artmind_id: id-1\n_domain: general\n---\n\n{body}"


def _index(repo):
    return _git(repo, "ls-files", "--stage")


def test_the_folder_extracted_from_the_merged_note_wins_whole(repo):
    """Theirs extracted v2 of the note, which the merge took; ours re-ran
    v1's extraction (different observations) and also touched a file theirs
    did not. The whole folder comes from theirs -- the file only ours
    changed, and the chunk cache only ours has, included."""
    base = {NOTE: _note("Body v1.\n"), **_doc_folder(obs="[0]")}
    ours = _doc_folder(obs="[1]", rel='["ours only"]', extra={f"{KG}/chunks/sha1/chunk_001.json": "{}"})
    theirs = {NOTE: _note("Body v2.\n"), **_doc_folder(body="Body v2.\n", obs="[2]")}
    _conflict(repo, base, ours, theirs)
    head = _git(repo, "rev-parse", "HEAD")

    report = vr.resolve(repo)

    [unit] = report["resolved"]
    assert (unit["unit"], unit["kind"], unit["side"]) == (KG, "kg", "theirs")
    assert "merged note" in unit["rule"]
    for name in ("document.json", "observations.json", "relationships.json", "chunks.json"):
        assert (repo / KG / name).read_text() == _git(repo, "show", f"theirs:{KG}/{name}")
    assert not (repo / KG / "chunks" / "sha1" / "chunk_001.json").exists()
    assert _git(repo, "diff", "--name-only", "--diff-filter=U") == ""
    assert _git(repo, "diff", "--cached", "--name-only", "theirs", "--", KG) == "", "the index holds theirs exactly"
    assert _git(repo, "rev-parse", "HEAD") == head, "never a commit"
    assert (repo / ".git" / "MERGE_HEAD").exists(), "Obsidian Git (or you) concludes the merge"


def _resolved_bytes(repo):
    return {p.relative_to(repo).as_posix(): p.read_bytes() for p in sorted((repo / KG).rglob("*")) if p.is_file()}


def test_both_directions_pick_the_same_content(repo):
    """Two machines extracted the same note version (both match its body):
    the greater fingerprint wins, whichever machine merges which -- so the
    two merge commits then merge with each other cleanly."""
    base = {NOTE: _note("Body v1.\n"), **_doc_folder(obs="[0]")}
    a = _doc_folder(obs='["machine A"]')
    b = _doc_folder(obs='["machine B"]')
    _conflict(repo, base, a, b)

    vr.resolve(repo)
    a_into = _resolved_bytes(repo)

    _git(repo, "merge", "--abort")
    _git(repo, "checkout", "-q", "theirs")
    _git(repo, "merge", "--no-edit", "main", check=False)
    report = vr.resolve(repo)
    b_into = _resolved_bytes(repo)

    assert "fingerprint" in report["resolved"][0]["rule"]
    assert a_into == b_into


def test_a_folder_whose_note_is_still_conflicted_waits(repo):
    base = {NOTE: _note("Body v1.\n"), **_doc_folder(obs="[0]")}
    ours = {NOTE: _note("Body ours.\n"), **_doc_folder(body="Body ours.\n", obs="[1]")}
    theirs = {NOTE: _note("Body theirs.\n"), **_doc_folder(body="Body theirs.\n", obs="[2]")}
    _conflict(repo, base, ours, theirs)
    before = (_index(repo), (repo / KG / "observations.json").read_bytes())

    report = vr.resolve(repo)

    assert report["resolved"] == []
    assert [p["unit"] for p in report["pending"]] == [KG]
    assert report["reported"] == [{"path": NOTE, "why": "your note -- resolve it in Obsidian"}]
    assert (_index(repo), (repo / KG / "observations.json").read_bytes()) == before

    (repo / NOTE).write_text(_note("Body ours.\n"))  # the user resolves the note in Obsidian
    again = vr.resolve(repo)

    assert [(u["unit"], u["side"]) for u in again["resolved"]] == [(KG, "ours")]


def test_a_note_deleted_by_the_merge_takes_the_side_without_its_folder(repo):
    base = {NOTE: _note("Body v1.\n"), **_doc_folder(obs="[0]")}
    ours = {NOTE: None, **{k: None for k in _doc_folder()}}  # archived on this machine
    theirs = _doc_folder(obs="[2]")  # re-extracted on the other
    _conflict(repo, base, ours, theirs)

    report = vr.resolve(repo)

    assert [(u["unit"], u["side"]) for u in report["resolved"]] == [(KG, "ours")]
    assert not (repo / KG).exists(), "no empty folder left behind"
    assert _git(repo, "diff", "--name-only", "--diff-filter=U") == ""


TABLE = ".artmind/data/structured_text/banking/accounts"


def _table(ingested_at, rows):
    meta = {"table": {"domain": "banking", "table_name": "accounts", "ingested_at": ingested_at}}
    return {f"{TABLE}.csv": "id\n" + rows, f"{TABLE}.meta.json": json.dumps(meta, sort_keys=True)}


def test_a_table_takes_the_later_refresh_csv_and_meta_together(repo):
    base = _table("2026-09-01T00:00:00", "1\n")
    ours = _table("2026-09-03T00:00:00", "1\n3\n")
    theirs = _table("2026-09-02T00:00:00", "1\n2\n")
    _conflict(repo, base, ours, theirs)

    report = vr.resolve(repo)

    assert [(u["unit"], u["kind"], u["side"]) for u in report["resolved"]] == [(TABLE, "table", "ours")]
    assert (repo / f"{TABLE}.csv").read_text() == "id\n1\n3\n"
    assert "2026-09-03" in (repo / f"{TABLE}.meta.json").read_text()


RECORD = ".artmind/data/curation/conflicts/c0ffee.json"


def _conflict_record(**fields):
    record = {"id": "c0ffee", "status": "open", "resolved_at": None, "resolution_reason": None,
              "detected_at": "2026-09-01T00:00:00+00:00", "claim_a": "x", "claim_b": "y"}
    record.update(fields)
    return json.dumps(record, indent=2, sort_keys=True) + "\n"


def test_two_machines_detecting_the_same_conflict_settle_on_one_record(repo):
    """Plan C leftover (b): both machines detected (and one resolved) the
    same conflict before pulling -- a git conflict on that one record file.
    The decision beats open, in either merge direction."""
    base = {"a.txt": "x"}
    ours = {RECORD: _conflict_record(detected_at="2026-09-05T00:00:00+00:00")}
    theirs = {RECORD: _conflict_record(status="resolved", resolved_at="2026-09-02T00:00:00+00:00",
                                       resolution_reason="board minutes")}
    _conflict(repo, base, ours, theirs)

    report = vr.resolve(repo)

    assert [(u["kind"], u["side"]) for u in report["resolved"]] == [("record", "theirs")]
    assert json.loads((repo / RECORD).read_text())["status"] == "resolved"


@pytest.mark.parametrize("ours, theirs, expected", [
    ({"status": "open"}, {"status": "dismissed", "resolved_at": "2026-01-01"}, "theirs"),
    ({"status": "resolved", "resolved_at": "2026-09-03"}, {"status": "resolved", "resolved_at": "2026-09-02"}, "ours"),
    ({"detected_at": "2026-09-01"}, {"detected_at": "2026-09-04"}, "theirs"),
])
def test_the_conflict_record_rule(ours, theirs, expected):
    sides = {
        "ours": {RECORD: _conflict_record(**ours).encode()},
        "theirs": {RECORD: _conflict_record(**theirs).encode()},
    }
    assert vr._decide_record(RECORD, sides)[0] == expected
    swapped = {"ours": sides["theirs"], "theirs": sides["ours"]}
    assert vr._decide_record(RECORD, swapped)[0] != expected, "the rule reads content, never the side's name"


def test_the_other_kinds_rules():
    sup = ".artmind/data/curation/supersessions/s1.json"
    manual = json.dumps({"id": "s1", "detected_by": "manual", "effective": "2026-01-01"}).encode()
    detected = json.dumps({"id": "s1", "detected_by": "notice", "effective": "2026-02-01"}).encode()
    assert vr._decide_record(sup, {"ours": {sup: detected}, "theirs": {sup: manual}})[0] == "theirs"

    syn = ".artmind/data/curation/syntheses/e1.json"
    old = json.dumps({"id": "e1", "created_at": "2026-09-01"}).encode()
    new = json.dumps({"id": "e1", "created_at": "2026-09-09"}).encode()
    assert vr._decide_record(syn, {"ours": {syn: new}, "theirs": {syn: old}})[0] == "ours"

    life = ".artmind/data/curation/lifecycle/l1.json"
    x, y = b'{"domain": "a"}', b'{"domain": "b"}'

    def picked(ours, theirs):
        sides = {"ours": {life: ours}, "theirs": {life: theirs}}
        return sides[vr._decide_record(life, sides)[0]][life]

    assert picked(x, y) == picked(y, x), "the greater fingerprint, whichever side holds it"
    assert vr._decide_record(life, {"ours": {}, "theirs": {life: x}})[0] == "theirs", "kept over deleted"


def test_human_curated_files_and_notes_are_reported_never_touched(repo):
    same_as = ".artmind/same_as.yaml"
    base = {same_as: "groups: []\n", NOTE: "base\n"}
    ours = {same_as: "groups: [a]\n", NOTE: "ours\n"}
    theirs = {same_as: "groups: [b]\n", NOTE: "theirs\n"}
    _conflict(repo, base, ours, theirs)
    before = (_index(repo), (repo / same_as).read_bytes(), (repo / NOTE).read_bytes())

    report = vr.resolve(repo)

    assert report["resolved"] == [] and report["remaining"] == 2
    assert sorted(r["path"] for r in report["reported"]) == [same_as, NOTE]
    assert (_index(repo), (repo / same_as).read_bytes(), (repo / NOTE).read_bytes()) == before


def test_a_dry_run_shows_every_pick_and_writes_nothing(repo):
    base = {NOTE: _note("Body v1.\n"), **_doc_folder(obs="[0]")}
    _conflict(repo, base, _doc_folder(obs="[1]"), _doc_folder(obs="[2]"))
    before = (_index(repo), (repo / KG / "observations.json").read_bytes())

    report = vr.resolve(repo, dry_run=True)

    assert report["dry_run"] is True
    assert [u["unit"] for u in report["resolved"]] == [KG]
    assert (_index(repo), (repo / KG / "observations.json").read_bytes()) == before


def test_refuses_without_a_merge_in_progress(repo):
    _write(repo, {"a.txt": "x"})
    _commit(repo, "base")

    with pytest.raises(vr.ResolveError, match="no merge is in progress"):
        vr.resolve(repo)


def test_the_write_guard_refuses_any_path_outside_artmind_data(repo):
    """Whatever the planner produced, a path outside `.artmind/data/` never
    reaches git: an uncommitted edit to the note survives the attempt."""
    _write(repo, {NOTE: "committed\n", ".artmind/same_as.yaml": "groups: []\n"})
    _commit(repo, "base")
    _write(repo, {NOTE: "my unsaved edit\n"})

    for present, absent in (([NOTE], []), ([], [NOTE]), ([".artmind/same_as.yaml"], []),
                            ([".artmind/data/../../notes/policy.md"], [])):
        with pytest.raises(vr.ResolveError, match="only writes under"):
            vr._stage_side(repo, "HEAD", present, absent)

    assert (repo / NOTE).read_text() == "my unsaved edit\n"
    assert _git(repo, "diff", "--cached", "--name-only") == ""


def test_after_resolve_the_merge_commits_and_vault_sync_preflight_passes(repo):
    from artmind import vault_sync

    base = {NOTE: _note("Body v1.\n"), **_doc_folder(obs="[0]"), RECORD: _conflict_record()}
    ours = {**_doc_folder(obs="[1]"), RECORD: _conflict_record(detected_at="2026-09-02")}
    theirs = {**_doc_folder(obs="[2]"), RECORD: _conflict_record(detected_at="2026-09-03")}
    _conflict(repo, base, ours, theirs)
    with pytest.raises(vault_sync.VaultSyncError):
        vault_sync.preflight(repo)

    vr.resolve(repo)
    _git(repo, "commit", "-qm", "merge (Obsidian Git)")

    vault_sync.preflight(repo)


@pytest.mark.parametrize("theirs_obs, ours_obs", [('["A"]', '["B"]'), ('["B"]', '["A"]')])
def test_a_crlf_note_is_hashed_as_ingest_hashes_it(repo, theirs_obs, ours_obs):
    """A note stored with CRLF line endings is hashed exactly as ingest hashed
    it (`read_text` translates the newlines, then `content_sha256`), so the
    folder extracted from that body still matches. Run with the observations
    swapped so the staging fingerprint alone would pick the wrong side in one
    of the two: the body match must decide, whichever side that is."""
    body_lf = "Line one.\nLine two.\n"
    crlf_note = _note(body_lf).replace("\n", "\r\n").encode()
    base = {NOTE: _note("Body v1.\n"), **_doc_folder(obs="[0]")}
    ours = _doc_folder(obs=ours_obs)
    theirs = {NOTE: crlf_note, **_doc_folder(body=body_lf, obs=theirs_obs)}
    _conflict(repo, base, ours, theirs)
    assert b"\r\n" in (repo / NOTE).read_bytes(), "the merged note really is CRLF on disk"
    assert (repo / NOTE).read_text().count("\r") == 0
    # ingest's own path: text read, body parsed, then hashed -- and hashing the
    # raw bytes would not agree, which is the trap this guards.
    assert compute_content_sha256(body_lf) != compute_content_sha256(body_lf.replace("\n", "\r\n"))

    report = vr.resolve(repo)

    [unit] = report["resolved"]
    assert (unit["side"], "merged note" in unit["rule"]) == ("theirs", True)
    assert (repo / KG / "observations.json").read_text() == theirs_obs


# ── review fixes (Plan D Task 7) ─────────────────────────────────────────────


def _bytes(files: dict) -> dict:
    return {k: (v.encode() if isinstance(v, str) else v) for k, v in files.items() if v is not None}


def _adversarial(make, key):
    """`make(i) -> (winner, loser)`; the first whose winner has the LESSER
    `key`. Fixtures that make the fingerprint-luck outcome the wrong one, so a
    rule only passes if it really decides."""
    for i in range(500):
        winner, loser = make(i)
        if key(winner) < key(loser):
            return winner, loser
    raise AssertionError("no adversarial fixture found")


# -- a lone `=======` is not a conflict marker --


SETEXT_BODY = "Policy\n=======\n\nBody ours.\n"


def test_a_setext_heading_in_a_resolved_note_is_not_a_conflict(repo):
    """The note conflicted; the user resolved it in Obsidian to a body that
    holds a setext heading (`=======` underline) and has not staged it. That
    line is not a conflict marker -- only a whole `<<<<<<<` .. `=======` ..
    `>>>>>>>` block is -- so the folder extracted from that body is resolved,
    not pending forever."""
    base = {NOTE: _note("Body v1.\n"), **_doc_folder(obs="[0]")}
    ours = {NOTE: _note("Body ours.\n"), **_doc_folder(body=SETEXT_BODY, obs="[1]")}
    theirs = {NOTE: _note("Body theirs.\n"), **_doc_folder(body="Body theirs.\n", obs="[2]")}
    _conflict(repo, base, ours, theirs)
    assert vr.resolve(repo, dry_run=True)["resolved"] == [], "the note still has real markers"

    (repo / NOTE).write_text(_note(SETEXT_BODY))
    report = vr.resolve(repo)

    assert [(u["unit"], u["side"]) for u in report["resolved"]] == [(KG, "ours")]
    assert report["pending"] == []


@pytest.mark.parametrize("rule", ["=======", "| a |\n=======\n| b |"])
def test_a_note_git_merged_cleanly_with_a_lone_separator_line_decides_by_body(repo, rule):
    """A note git merged without conflict (so it is not unmerged) that holds a
    `=======` line -- a setext heading, an ASCII table -- decides its folder
    by body like any other note."""
    body = f"Title\n{rule}\n\nText.\n"
    base = {NOTE: _note(body), **_doc_folder(body=body, obs="[0]")}
    ours = _doc_folder(body="Something else.\n", obs="[1]")
    theirs = _doc_folder(body=body, obs="[2]")
    _conflict(repo, base, ours, theirs)

    [unit] = vr.resolve(repo)["resolved"]

    assert (unit["side"], "merged note" in unit["rule"]) == ("theirs", True)


def test_a_note_that_quotes_conflict_markers_but_is_not_unmerged_decides_by_body(repo):
    """git no longer lists the note as unmerged (it was merged, or resolved and
    staged); a whole marker block left in its text -- a note ABOUT merge
    conflicts -- is content, not a pending conflict."""
    body = "How to read a conflict:\n\n<<<<<<< HEAD\nmine\n=======\ntheirs\n>>>>>>> branch\n"
    base = {NOTE: _note(body), **_doc_folder(body=body, obs="[0]")}
    _conflict(repo, base, _doc_folder(body="Other.\n", obs="[1]"), _doc_folder(body=body, obs="[2]"))
    assert NOTE not in _git(repo, "diff", "--name-only", "--diff-filter=U")

    [unit] = vr.resolve(repo)["resolved"]

    assert (unit["side"], "merged note" in unit["rule"]) == ("theirs", True)


# -- resumable staging --


class Boom(Exception):
    pass


_REAL_RUN, _REAL_PRUNE = vr.run_command, vr._prune_empty_dirs


def _instrument(monkeypatch, fail_at=None):
    """Record every mutating step (`git checkout`, `git add`, the directory
    prune -- each before and after), raising `Boom` at event `fail_at`."""
    events = []

    def tick(name):
        events.append(name)
        if len(events) - 1 == fail_at:
            raise Boom(name)

    def run(cmd, *args, **kwargs):
        if len(cmd) > 1 and cmd[1] in ("checkout", "add"):
            tick(f"before git {cmd[1]}")
            out = _REAL_RUN(cmd, *args, **kwargs)
            tick(f"after git {cmd[1]}")
            return out
        return _REAL_RUN(cmd, *args, **kwargs)

    def prune(*args, **kwargs):
        tick("before prune")
        _REAL_PRUNE(*args, **kwargs)
        tick("after prune")

    monkeypatch.setattr(vr, "run_command", run)
    monkeypatch.setattr(vr, "_prune_empty_dirs", prune)
    return events


CACHE = f"{KG}/chunks/sha1/chunk_001.json"


def _scenario_theirs_and_an_ours_only_file(root):
    """Theirs is picked; ours alone added a chunk-cache file (git merged it in
    cleanly, so it is staged at stage 0 and NOT unmerged)."""
    _init_repo(root)
    base = {NOTE: _note("Body v1.\n"), **_doc_folder(obs="[0]")}
    ours = _doc_folder(obs="[1]", extra={CACHE: "{}"})
    theirs = {NOTE: _note("Body v2.\n"), **_doc_folder(body="Body v2.\n", obs="[2]")}
    _conflict(root, base, ours, theirs)
    return root, "theirs"


def _scenario_ours_and_a_theirs_only_file(root):
    _init_repo(root)
    base = {NOTE: _note("Body v1.\n"), **_doc_folder(obs="[0]")}
    ours = _doc_folder(obs="[1]")
    theirs = _doc_folder(body="Other.\n", obs="[2]", extra={CACHE: "{}"})
    _conflict(root, base, ours, theirs)
    return root, "ours"


def _scenario_note_deleted_and_a_theirs_only_file(root):
    _init_repo(root)
    base = {NOTE: _note("Body v1.\n"), **_doc_folder(obs="[0]")}
    ours = {NOTE: None, **{k: None for k in _doc_folder()}}
    theirs = _doc_folder(obs="[2]", extra={CACHE: "{}"})
    _conflict(root, base, ours, theirs)
    return root, "ours"


SCENARIOS = [
    _scenario_theirs_and_an_ours_only_file,
    _scenario_ours_and_a_theirs_only_file,
    _scenario_note_deleted_and_a_theirs_only_file,
]


def _snapshot(repo):
    data = repo / ".artmind" / "data"
    return (
        sorted((p.relative_to(repo).as_posix(), p.read_bytes() if p.is_file() else None) for p in data.rglob("*")),
        _git(repo, "ls-files", "--stage", "--", ".artmind"),
        _git(repo, "diff", "--name-only", "--diff-filter=U"),
    )


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda f: f.__name__.removeprefix("_scenario_"))
def test_an_interrupted_resolve_is_finished_by_a_rerun_byte_for_byte(tmp_path, monkeypatch, scenario):
    """`vault resolve` killed at ANY step -- after a checkout, after the
    deletions, before an add -- and run again leaves exactly what an
    uninterrupted run leaves: the chosen side's files, byte for byte, with none
    of the other side's. (A file only the losing side has is staged at stage 0,
    not unmerged, so a rerun that found the unit by its unmerged paths alone
    would leave it behind.)"""
    import paths

    monkeypatch.setattr(paths, "WORKER_PID_FILE", tmp_path / "worker.pid")
    clean, side = scenario(tmp_path / "clean")
    events = _instrument(monkeypatch)
    vr.resolve(clean)
    expected = _snapshot(clean)
    assert len(events) >= 6, events
    rev = {"ours": "HEAD", "theirs": "theirs"}[side]
    assert _git(clean, "diff", "--cached", "--name-only", rev, "--", KG) == "", "the clean run holds the whole side"
    assert not (clean / CACHE).exists() and CACHE not in _git(clean, "ls-files"), "the other side's file is gone"

    for k, event in enumerate(events):
        root, _ = scenario(tmp_path / f"interrupted{k}")
        _instrument(monkeypatch, fail_at=k)
        with pytest.raises(Boom):
            vr.resolve(root)
        _instrument(monkeypatch)
        vr.resolve(root)
        assert _snapshot(root) == expected, f"interrupted at event {k} ({event})"
        assert _git(root, "diff", "--cached", "--name-only", rev, "--", KG) == ""


# -- literal pathspecs --


def test_a_glob_named_file_is_one_file_never_a_pattern(repo):
    """`[a].bin` is a name, not a character class. Picking it with a glob
    pathspec (`GIT_LITERAL_PATHSPECS` off) would also check out `a.bin`
    from the same side, reverting a sibling git had merged to the other one."""
    star, real = ".artmind/data/[a].bin", ".artmind/data/a.bin"
    base = {star: "x", real: "1"}
    ours = {star: "kept and edited"}
    theirs = {star: None, real: "2"}  # theirs deleted `[a].bin` and edited `a.bin`
    _conflict(repo, base, ours, theirs)
    assert (repo / real).read_text() == "2", "git merged the sibling to theirs"

    report = vr.resolve(repo)

    assert [(u["unit"], u["side"]) for u in report["resolved"]] == [(star, "ours")]
    assert (repo / star).read_text() == "kept and edited"
    assert (repo / real).read_text() == "2", "the sibling matched by the glob was never touched"
    assert _git(repo, "diff", "--cached", "--name-only", "theirs", "--", real) == ""


def test_a_star_named_file_of_an_unpicked_side_is_deleted_alone(repo):
    """The picked side lacks `chunks/*`; `git add -- chunks/*` as a glob would
    stage every file beside it -- including one artmind never owned."""
    star = f"{KG}/chunks/*"
    stray = f"{KG}/chunks/stray.txt"
    base = {NOTE: _note("Body v1.\n"), **_doc_folder(obs="[0]")}
    ours = {NOTE: None, **{k: None for k in _doc_folder()}}
    theirs = _doc_folder(obs="[2]", extra={star: "{}"})
    _conflict(repo, base, ours, theirs)
    _write(repo, {stray: "mine, untracked"})

    vr.resolve(repo)

    assert not (repo / star).exists()
    assert (repo / stray).read_text() == "mine, untracked"
    assert stray not in _git(repo, "ls-files", "--stage"), "never staged by a glob"
    assert _git(repo, "diff", "--name-only", "--diff-filter=U") == ""


# -- rules that decide by content, not by fingerprint luck --


def _side(files):
    return _bytes(files)


def _fp(files):
    from artmind.sync_state import staging_fingerprint

    return staging_fingerprint(lambda name: files.get(f"{KG}/{name}")) or ""


def _kg_files(obs, body="Body v1.\n", extra=None):
    return _bytes(_doc_folder(body=body, obs=obs, extra=extra))


def _decide_kg(vault_dir, ours, theirs):
    paths = sorted(set(ours) | set(theirs))
    return vr._decide_kg(vault_dir, KG, paths, {"ours": ours, "theirs": theirs}, vr._NoteIndex(vault_dir))


@pytest.fixture()
def note_dir(tmp_path):
    (tmp_path / "notes").mkdir()
    (tmp_path / NOTE).write_text(_note("Body v1.\n"))
    return tmp_path


def _picked(decide, ours, theirs):
    """The winning CONTENT, from both merge orientations -- they must agree."""
    fwd = {"ours": ours, "theirs": theirs}[decide(ours, theirs)[0]]
    rev = {"ours": theirs, "theirs": ours}[decide(theirs, ours)[0]]
    assert fwd == rev, "the pick depends on which side is which"
    return fwd


def test_the_body_match_beats_a_greater_fingerprint(note_dir):
    """The side extracted from the merged note's body wins even when its
    fingerprint AND digest are the lesser ones, in either orientation."""
    right, wrong = _adversarial(
        lambda i: (_kg_files(f"[{i}]", body="Body v1.\n"), _kg_files(f"[{i + 1000}]", body="Other body.\n")),
        lambda files: (_fp(files), vr._digest(files, sorted(files))),
    )
    assert _picked(lambda a, b: _decide_kg(note_dir, a, b), right, wrong) is right


def test_the_fingerprint_beats_a_greater_digest(note_dir):
    """Neither side matches the note: the greater staging fingerprint wins,
    whatever the digest of the chunk cache beside it says."""
    def make(i):
        a = _kg_files(f"[{i}]", body="Not the note.\n", extra={CACHE: str(i)})
        b = _kg_files(f"[{i + 1000}]", body="Not the note.\n", extra={CACHE: str(i + 1000)})
        return (a, b) if _fp(a) > _fp(b) else (b, a)

    paths = sorted({f"{KG}/{n}" for n in ("document.json", "chunks.json", "observations.json", "relationships.json")} | {CACHE})
    high, low = _adversarial(make, lambda files: vr._digest(files, paths))
    assert _fp(high) > _fp(low) and vr._digest(high, paths) < vr._digest(low, paths)
    assert _picked(lambda a, b: _decide_kg(note_dir, a, b), high, low) is high


def test_equal_fingerprints_fall_to_the_greater_digest(note_dir):
    """Same four staged files, a chunk cache that differs: the greater digest
    of the whole unit wins, whichever side holds it."""
    a = _kg_files("[1]", body="Not the note.\n", extra={CACHE: "a"})
    b = _kg_files("[1]", body="Not the note.\n", extra={CACHE: "b"})
    assert _fp(a) == _fp(b)
    paths = sorted(set(a) | set(b))
    winner = a if vr._digest(a, paths) > vr._digest(b, paths) else b
    assert _picked(lambda x, y: _decide_kg(note_dir, x, y), a, b) is winner


def test_a_merge_deleted_note_takes_the_side_without_the_folder_either_way(note_dir):
    (note_dir / NOTE).unlink()
    folder = _kg_files("[1]")
    for ours, theirs, expected in (({}, folder, "ours"), (folder, {}, "theirs")):
        assert _decide_kg(note_dir, ours, theirs)[0] == expected


def test_two_sides_naming_different_notes_pick_the_same_content_either_way(tmp_path):
    """Ours' document.json names `notes/a.md`, theirs' names `notes/b.md`, and
    both notes exist. Whichever machine merged which, the same note decides
    (candidates are ordered by content, not by side), so the same folder wins."""
    (tmp_path / "notes").mkdir()
    (tmp_path / "notes/a.md").write_text(_note("Body A.\n"))
    (tmp_path / "notes/b.md").write_text(_note("Body B.\n"))

    def files(name, body):
        folder = _doc_folder(body=body, obs=f'["{name}"]')
        document = json.loads(folder[f"{KG}/document.json"])
        document["source_path"] = f"notes/{name}.md"
        folder[f"{KG}/document.json"] = json.dumps(document)
        return _bytes(folder)

    a, b = files("a", "Body A.\n"), files("b", "Body B.\n")
    forward = _decide_kg(tmp_path, a, b)[0]
    backward = _decide_kg(tmp_path, b, a)[0]
    assert {"ours": a, "theirs": b}[forward] == {"ours": b, "theirs": a}[backward]


def _table_files(ingested_at, tag):
    meta = {"table": {"domain": "banking", "table_name": "accounts", "ingested_at": ingested_at}}
    return _bytes({f"{TABLE}.csv": f"id\n{tag}\n", f"{TABLE}.meta.json": json.dumps(meta, sort_keys=True)})


TABLE_PATHS = [f"{TABLE}.csv", f"{TABLE}.meta.json"]


def _decide_table(ours, theirs):
    return vr._decide_table(TABLE, TABLE_PATHS, {"ours": ours, "theirs": theirs})


def _tdigest(files):
    return vr._digest(files, TABLE_PATHS)


@pytest.mark.parametrize("later, earlier", [
    ("2026-09-03T00:00:00", "2026-09-02T00:00:00"),
    ("2026-09-02T10:00:00.5+00:00", "2026-09-02T10:00:00Z"),  # text order says Z is later
    ("2026-09-02T08:00:00+00:00", "2026-09-02T10:00:00+05:00"),  # 05:00Z is earlier than 08:00Z
    ("2026-09-02T00:00:00", "not a time"),  # unparsable sorts lowest
    ("2026-09-02T00:00:00", None),
])
def test_the_later_refresh_wins_over_a_greater_digest(later, earlier):
    win, lose = _adversarial(lambda i: (_table_files(later, i), _table_files(earlier, i)), _tdigest)
    assert _picked(_decide_table, win, lose) is win


def test_the_same_instant_in_two_spellings_falls_to_the_greater_digest():
    """`...10:00:00Z` and `...10:00:00+00:00` are one instant: the digest
    decides, in either orientation."""
    a, b = _table_files("2026-09-02T10:00:00Z", 1), _table_files("2026-09-02T10:00:00+00:00", 1)
    winner = a if _tdigest(a) > _tdigest(b) else b
    assert _picked(_decide_table, a, b) is winner


def test_a_table_only_one_side_kept_is_that_side_with_that_reason():
    kept = _table_files("2026-09-02T00:00:00", "a")
    for ours, theirs, expected in ((kept, {}, "ours"), ({}, kept, "theirs")):
        assert _decide_table(ours, theirs) == (expected, "the side that kept the table")


def test_equal_refreshes_fall_to_the_greater_digest():
    a, b = _table_files("2026-09-02T00:00:00", "a"), _table_files("2026-09-02T00:00:00", "b")
    winner = a if _tdigest(a) > _tdigest(b) else b
    assert _picked(_decide_table, a, b) is winner


def _rec(kind, **fields):
    return json.dumps({"id": "r1", **fields}, sort_keys=True).encode()


def _decide_rec(kind):
    unit = f".artmind/data/curation/{kind}/r1.json"

    def decide(ours, theirs):
        return vr._decide_record(unit, {"ours": {unit: ours}, "theirs": {unit: theirs}})

    return decide


def _fingerprint(data):
    from artmind import curation_records

    return curation_records.fingerprint(data)


@pytest.mark.parametrize("kind, winner, loser", [
    ("conflicts", {"status": "resolved", "resolved_at": "2026-09-01T00:00:00Z"}, {"status": "open"}),
    ("conflicts", {"status": "open", "detected_at": "2026-09-02T10:00:00.5+00:00"}, {"status": "open", "detected_at": "2026-09-02T10:00:00Z"}),
    ("conflicts", {"status": "resolved", "resolved_at": "2026-09-02T09:00:00+00:00"}, {"status": "resolved", "resolved_at": "2026-09-02T12:00:00+05:00"}),
    ("conflicts", {"status": "resolved", "resolved_at": "2026-09-02"}, {"status": "resolved", "resolved_at": None, "detected_at": "junk"}),
    # resolved_at, not detected_at, is the last change when both are there
    ("conflicts", {"status": "resolved", "resolved_at": "2026-09-05", "detected_at": "2026-09-01"},
     {"status": "resolved", "resolved_at": "2026-09-03", "detected_at": "2026-09-04"}),
    ("supersessions", {"detected_by": "manual"}, {"detected_by": "notice"}),
    ("syntheses", {"created_at": "2026-09-02T10:00:00.5+00:00"}, {"created_at": "2026-09-02T10:00:00Z"}),
    ("syntheses", {"created_at": "2026-09-02T00:00:00"}, {"created_at": "junk"}),
])
def test_each_record_rule_decides_when_the_fingerprint_would_not(kind, winner, loser):
    """The rule under test picks the winner even though the winner has the
    LESSER record fingerprint -- so the fingerprint cannot have decided."""
    win, lose = _adversarial(
        lambda i: (_rec(kind, note=i, **winner), _rec(kind, note=i, **loser)), _fingerprint
    )
    assert _picked(_decide_rec(kind), win, lose) is win


def test_equal_rule_keys_fall_to_the_greater_record_fingerprint():
    for kind in ("conflicts", "supersessions", "syntheses", "lifecycle"):
        a, b = _rec(kind, x=1), _rec(kind, x=2)
        assert _picked(_decide_rec(kind), a, b) == max(a, b, key=_fingerprint), kind


def test_the_other_files_rule_is_the_greater_content_hash():
    unit = ".artmind/data/converted/x.md"

    def decide(ours, theirs):
        return vr._decide_file(unit, {"ours": {unit: ours}, "theirs": {unit: theirs}})

    import hashlib

    a, b = b"alpha", b"beta"
    assert _picked(decide, a, b) == max(a, b, key=lambda d: hashlib.sha256(d).hexdigest())


# -- the note deleted by the merge, found by id in the merged heads --


def test_a_legacy_folder_without_source_path_follows_its_deleted_note(repo):
    """A `document.json` from before `source_path` names no note; the note
    (fat frontmatter, carrying `_artmind_id`) was deleted by the merge. It is
    found in the merged heads by its id, so the side without the folder wins
    instead of the greater fingerprint reviving a folder for a deleted note."""
    def legacy(obs):
        folder = _doc_folder(obs=obs)
        document = json.loads(folder[f"{KG}/document.json"])
        del document["source_path"], document["source_type"]
        folder[f"{KG}/document.json"] = json.dumps(document)
        return folder

    fat_note = "---\n_artmind_id: id-1\n_domain: general\n_content_sha256: abc\n_version: 1\n---\n\nBody v1.\n"
    base = {NOTE: fat_note, **legacy("[0]")}
    ours = {NOTE: None, **{k: None for k in legacy("[0]")}}
    theirs = legacy("[9]")
    _conflict(repo, base, ours, theirs)

    [unit] = vr.resolve(repo)["resolved"]

    assert (unit["side"], "deleted its note" in unit["rule"]) == ("ours", True)
    assert not (repo / KG).exists()


# -- leftover shells --


@pytest.mark.parametrize("shape", ["file", "empty-dir"])
def test_a_removed_folder_leaves_no_embeddings_shell(repo, shape):
    """`embeddings.json` is a gitignored local sidecar. When the picked side
    has no such folder, nothing but that sidecar is left in it: gone too."""
    base = {NOTE: _note("Body v1.\n"), **_doc_folder(obs="[0]")}
    ours = {NOTE: None, **{k: None for k in _doc_folder()}}
    _conflict(repo, base, ours, _doc_folder(obs="[2]"))
    sidecar = repo / KG / "embeddings.json"
    if shape == "file":
        sidecar.write_text("[[0.1]]")
    else:
        sidecar.mkdir()

    vr.resolve(repo)

    assert not (repo / KG).exists()


def test_the_sidecar_stays_when_the_folder_does(repo):
    base = {NOTE: _note("Body v1.\n"), **_doc_folder(obs="[0]")}
    _conflict(repo, base, _doc_folder(obs="[1]"), {NOTE: _note("Body v2.\n"), **_doc_folder(body="Body v2.\n", obs="[2]")})
    (repo / KG / "embeddings.json").write_text("[[0.1]]")

    vr.resolve(repo)

    assert (repo / KG / "embeddings.json").read_text() == "[[0.1]]"


# -- the ingest worker --


def test_resolve_refuses_while_the_ingest_worker_runs_but_a_dry_run_reads(repo, tmp_path):
    import fcntl
    import os

    base = {NOTE: _note("Body v1.\n"), **_doc_folder(obs="[0]")}
    _conflict(repo, base, _doc_folder(obs="[1]"), _doc_folder(obs="[2]"))
    before = (_index(repo), (repo / KG / "observations.json").read_bytes())

    with open(tmp_path / "worker.pid", "w") as pid_file:  # what the worker holds
        fcntl.flock(pid_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        pid_file.write(str(os.getpid()))
        pid_file.flush()

        with pytest.raises(vr.ResolveError, match="ingest worker is running"):
            vr.resolve(repo)
        assert (_index(repo), (repo / KG / "observations.json").read_bytes()) == before
        assert vr.resolve(repo, dry_run=True)["resolved"]

    assert vr.resolve(repo)["resolved"], "the worker stopped: resolve runs"
