"""The `--compact` JSON the Obsidian plugin reads, captured from the real CLI
(spec 2026-09-30 §9).

Each scenario builds a throwaway vault, runs one `artmind` command through
`CliRunner` exactly as the plugin would (`cwd` = the vault root), and
compares what it printed with the fixture checked in under
`obsidian/artmind-obsidian/test/fixtures/<name>.json` -- the same file the
plugin's `VaultState`/`ArtmindCli` tests load. A CLI output change therefore
fails HERE, before it silently breaks the plugin.

When a change is intended, regenerate and then run the plugin's tests:

    ARTMIND_REGEN_PLUGIN_FIXTURES=1 uv run --group dev pytest test/test_plugin_fixtures.py -q
    just obsidian-plugin-test

A fixture is `{"args", "exit_code", "json", "stderr"}`: `json` is stdout
parsed (null when stdout is empty), `stderr` the error box rich-click prints
on a failure (null on success), exactly as the plugin receives it -- log
lines above the box are dropped. Values that differ per run are normalised
the same way on both sides: temp paths, commit shas (each distinct sha
becomes `1111…`, `2222…`, in order of appearance, abbreviations included),
uuids and timestamps.

Hermetic: Neo4j is an in-memory bookmark/fingerprint store plus recorded
writes (as in test_vault_sync.py), docling and the worker are stood in for,
and git, the registry, DuckDB and every artmind file are real.
"""
from __future__ import annotations

import fcntl
import json
import os
import re
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

FIXTURES = Path(__file__).resolve().parent.parent / "obsidian" / "artmind-obsidian" / "test" / "fixtures"
REGENERATE = os.environ.get("ARTMIND_REGEN_PLUGIN_FIXTURES") == "1"

MANIFEST = """\
ingest:
  mappings:
    - path: "Notes/**"
      domain: general
    - path: "Team/**"
      domain: general
"""

TEAM_MAPPING = """\
table: "team"
domain: general
entities:
  person:
    class: PERSON
    name: "{full_name}"
    properties:
      full_name: {column: name, required: true}
      affiliation: employer
      seniority: level
  org:
    class: ORGANIZATION
    name: "{org_name}"
    properties:
      org_name: {column: employer, required: true}
  manager:
    class: PERSON
    lookup: {entity: person, column: manager, match_column: name, on_unresolved: stub, on_ambiguous: skip}
    name: "{manager_name}"
    properties:
      manager_name: manager
relationships:
  - {source: person, rel_type: works_at, target: org}
  - {source: person, rel_type: reports_to, target: manager}
"""


# ── the vault ────────────────────────────────────────────────────────────────


class FakeGraph:
    """What one Neo4j holds for `vault sync`: bookmarks and fingerprints."""

    def __init__(self):
        self.bookmarks: dict[str, str] = {}
        self.fingerprints: dict[str, str | None] = {}
        self.unreachable = False

    def read_graph_bookmark(self, vault_id, *, timeout=None):
        if self.unreachable:
            raise OSError("Couldn't connect to 127.0.0.1:7687 (reason [Errno 61] Connection refused)")
        return self.bookmarks.get(vault_id)

    def write_graph_bookmark(self, vault_id, commit):
        self.bookmarks[vault_id] = commit

    def read_document_fingerprints(self, doc_ids, *, timeout=None):
        return {d: self.fingerprints[d] for d in doc_ids if d in self.fingerprints}


class Vault:
    def __init__(self, root: Path, graph: FakeGraph):
        self.root = root
        self.graph = graph

    def git(self, *args: str, check: bool = True) -> str:
        return subprocess.run(
            ["git", *args], cwd=self.root, capture_output=True, text=True, check=check
        ).stdout.strip()

    def write(self, rel: str, content: str | bytes) -> Path:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding="utf-8")
        return path

    def commit(self, message: str) -> str:
        self.git("add", "-A")
        self.git("commit", "-qm", message)
        return self.git("rev-parse", "HEAD")

    def run(self, *args: str) -> dict:
        from artmind.cli import cli

        result = CliRunner().invoke(cli, list(args), catch_exceptions=False)
        stdout = result.stdout.strip()
        box = result.stderr[result.stderr.find("╭─ Error"):] if "╭─ Error" in result.stderr else ""
        return {
            "args": list(args),
            "exit_code": result.exit_code,
            "json": json.loads(stdout) if stdout else None,
            "stderr": box.rstrip() or None,
        }

    def sync_bootstrap(self) -> None:
        assert self.run("vault", "sync", "--bootstrapSynced", "--compact")["exit_code"] == 0

    def kg_doc(self, name: str, doc_id: str) -> None:
        """A committed KG staging folder, as an extraction leaves one."""
        folder = f".artmind/data/kg/general/{name}"
        self.write(f"{folder}/document.json", json.dumps({"id": doc_id, "name": f"{name}.md"}))
        for part in ("chunks", "observations", "relationships"):
            self.write(f"{folder}/{part}.json", "[]")

    def conflict(self, base: dict, ours: dict, theirs: dict) -> None:
        """`base` committed, `theirs` on a branch, `ours` on main, then the
        branch merged into main -- the merge left in progress."""
        for rel, content in base.items():
            self.write(rel, content)
        self.commit("base")
        self.git("checkout", "-qb", "theirs")
        for rel, content in theirs.items():
            self.write(rel, content)
        self.commit("theirs")
        self.git("checkout", "-q", "main")
        for rel, content in ours.items():
            self.write(rel, content)
        self.commit("ours")
        self.git("merge", "--no-edit", "theirs", check=False)
        assert (self.root / ".git" / "MERGE_HEAD").exists()


@pytest.fixture()
def vault(tmp_path, monkeypatch):
    """An `artmind init`ed git vault with one commit, as the plugin finds it,
    every path constant pointed into it, and Neo4j in memory."""
    import artmind.db as db
    import artmind.document_identity as di
    import artmind.ingest as ing
    import artmind.table2graph as t2g
    import artmind.vault_git as vg
    import paths
    from artmind import sync_state
    from artmind.cli import cli

    root = tmp_path / "vault"
    root.mkdir()
    monkeypatch.chdir(root)
    monkeypatch.setenv("COLUMNS", "80")
    monkeypatch.setenv("ARTMIND_KG_NEO4J_URI", "neo4j://127.0.0.1:7687")
    monkeypatch.setenv("ARTMIND_KG_NEO4J_DATABASE", "neo4j")
    for key, value in {
        "GIT_AUTHOR_DATE": "2026-09-30T12:00:00+00:00",
        "GIT_COMMITTER_DATE": "2026-09-30T12:00:00+00:00",
    }.items():
        monkeypatch.setenv(key, value)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "a@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Laptop A"], cwd=root, check=True)
    assert CliRunner().invoke(cli, ["init"]).exit_code == 0

    data = root / ".artmind" / "data"
    monkeypatch.setattr(paths, "ARTMIND_VAULT_DIR", root)
    monkeypatch.setattr(paths, "KG_DIR", data / "kg")
    monkeypatch.setattr(paths, "STRUCTURED_TEXT_DIR", data / "structured_text")
    monkeypatch.setattr(paths, "TABLE_MAPPINGS_DIR", root / ".artmind" / "domains" / "table_mappings")
    monkeypatch.setattr(paths, "STRUCTURED_DIR", tmp_path / "structured")
    monkeypatch.setattr(paths, "WORKER_PID_FILE", tmp_path / "worker.pid")
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "document_registry.db")
    for module in (ing, di, vg):
        monkeypatch.setattr(module, "ARTMIND_VAULT_DIR", root)
    monkeypatch.setattr(ing, "KG_DIR", data / "kg")
    monkeypatch.setattr(ing, "MARKDOWNS_DIR", data / "documents" / "markdowns")
    monkeypatch.setattr(ing, "_convert_binary_via_docling", lambda dest, model: (f"# {dest.stem}\n", {}))

    graph = FakeGraph()
    for name in ("read_graph_bookmark", "write_graph_bookmark", "read_document_fingerprints"):
        monkeypatch.setattr(sync_state, name, getattr(graph, name))
    monkeypatch.setattr(ing, "_write_to_neo4j", lambda folder, domain, defer_rebuild=False: {
        "chunks": 1, "observations": 2, "relationships": 1, "retracted": 0,
        "deferred_keys": [], "unembedded_chunk_ids": [],
    })
    monkeypatch.setattr(ing, "retract_document", lambda doc_id, domain: {"doc_id": doc_id, "affected_keys": []})
    monkeypatch.setattr(t2g, "_rebuild_in_batches", lambda keys, groups=None: {"rebuilt": len(keys), "keys": len(keys)})
    monkeypatch.setattr(ing, "_sweep_embeddings", lambda domain, keys, strict=False: 0)
    monkeypatch.setattr(ing, "_sweep_chunk_embeddings", lambda chunk_ids=None, domain=None, strict=False: 0)

    import artmind.cli as cli_module

    monkeypatch.setattr(cli_module, "_ensure_worker_running", lambda: None)

    v = Vault(root, graph)
    v.write(".artmind/vault.yaml", (root / ".artmind" / "vault.yaml").read_text() + MANIFEST)
    v.write("Notes/welcome.md", "Welcome to the vault.\n")
    v.commit("init")
    return v


def _hold_worker_lock(vault: Vault):
    """A live ingest worker, as `worker_pid.live_pid` sees one: an exclusive
    flock on the vault's pid file."""
    from artmind.vault import VaultLayout

    pid_file = VaultLayout(vault.root).worker_pid
    handle = open(pid_file, "a+")
    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    handle.write("4242")
    handle.flush()
    return handle


def _behind(vault: Vault) -> None:
    """Synced, then the other laptop's commit pulled: two documents and one
    structured table this machine's stores have not applied."""
    from artmind.structured.pipeline import ingest_structured_file

    vault.sync_bootstrap()
    vault.kg_doc("quarterly_review", "doc-quarterly-review")
    vault.kg_doc("hiring_plan", "doc-hiring-plan")
    source = vault.write("Team/team.csv", "name,employer,level\nAnn Lee,Acme,senior\nBo Chan,Acme,junior\n")
    ingest_structured_file(source, "general")
    vault.commit("from laptop B")


def _table_registered_and_mapped(vault: Vault) -> None:
    from artmind.structured.pipeline import ingest_structured_file

    source = vault.write(
        "Team/team.csv",
        "name,employer,level,manager\n"
        "Ann Lee,Acme,senior,\n"
        "Bo Chan,Initech,junior,Ann Lee\n"
        ",Nobody,junior,\n"
        "Cy Dee,Acme,junior,Zed Unknown\n",
    )
    ingest_structured_file(source, "general")
    vault.write(".artmind/domains/table_mappings/team.yaml", TEAM_MAPPING)


def _jobs(states: list[tuple[str, str | None]], *, status: str, processed: int) -> str:
    """A job as the worker leaves it: its row and one row per file."""
    from artmind.jobs import _create_job, _update_job_file_status, _update_job_status

    files = [f"/vault/Notes/file{i}.md" for i in range(1, len(states) + 1)]
    job_id = _create_job(files, domain=None)
    _update_job_status(job_id, status=status, processed_count=processed, started_at="2026-09-30T12:00:00")
    if status in ("completed", "failed"):
        _update_job_status(job_id, completed_at="2026-09-30T12:05:00")
    from artmind.ingest import _init_chunk_rows, _update_chunk_step

    for i, (path, (file_status, error)) in enumerate(zip(files, states), start=1):
        processing = file_status == "processing"
        _update_job_file_status(
            job_id, path, status=file_status,
            current_step="extract_kg" if processing else None,
            doc_sha256=f"sha-file{i}" if processing else None,
            error_message=error,
            # Varied, so a view scaling bars to the job's largest file has
            # something to scale.
            kg_counts={"entities": 10 * i + 2, "relationships": 6 * i + 1} if file_status == "completed" else None,
        )
        if processing:
            # Mid-extraction: 7 chunks, entities done on 4, relationships on 3.
            _init_chunk_rows(f"sha-file{i}", f"doc-file{i}", 7)
            for seq in range(1, 5):
                _update_chunk_step(f"sha-file{i}", seq, "entities", "ok")
            for seq in range(1, 4):
                _update_chunk_step(f"sha-file{i}", seq, "relationships", "ok")
    return job_id


# ── the scenarios: one per fixture ───────────────────────────────────────────


def s_vault_status_no_bookmark(vault):
    return vault.run("vault", "status", "--compact")


def s_vault_status_in_sync(vault):
    vault.sync_bootstrap()
    return vault.run("vault", "status", "--compact")


def s_vault_status_behind(vault):
    _behind(vault)
    return vault.run("vault", "status", "--compact")


def s_vault_status_merge_conflict(vault):
    vault.sync_bootstrap()
    vault.conflict(
        {".artmind/same_as.yaml": "groups: []\n"},
        {".artmind/same_as.yaml": "groups: [[a, b]]\n"},
        {".artmind/same_as.yaml": "groups: [[c, d]]\n"},
    )
    return vault.run("vault", "status", "--compact")


def s_vault_status_neo4j_unreachable(vault):
    vault.sync_bootstrap()
    vault.graph.unreachable = True
    return vault.run("vault", "status", "--compact")


def s_vault_sync_success(vault):
    _behind(vault)
    return vault.run("vault", "sync", "--compact")


def s_vault_sync_refusal_worker_running(vault):
    vault.sync_bootstrap()
    handle = _hold_worker_lock(vault)
    try:
        return vault.run("vault", "sync", "--compact")
    finally:
        handle.close()


def s_vault_sync_refusal_merge_in_progress(vault):
    vault.sync_bootstrap()
    vault.conflict(
        {"Notes/plan.md": "Plan v1.\n"},
        {"Notes/plan.md": "Plan, ours.\n"},
        {"Notes/plan.md": "Plan, theirs.\n"},
    )
    return vault.run("vault", "sync", "--compact")


def s_vault_sync_refusal_artmind_conflicts(vault):
    """Unmerged paths under `.artmind/` with no merge in progress: what a
    conflicting `git stash pop` leaves."""
    vault.sync_bootstrap()
    vault.write(".artmind/same_as.yaml", "groups: []\n")
    vault.commit("same_as")
    vault.write(".artmind/same_as.yaml", "groups: [[a, b]]\n")
    vault.git("stash")
    vault.write(".artmind/same_as.yaml", "groups: [[c, d]]\n")
    vault.commit("other edit")
    vault.git("stash", "pop", check=False)
    assert not (vault.root / ".git" / "MERGE_HEAD").exists()
    return vault.run("vault", "sync", "--compact")


def s_vault_sync_refusal_bookmark_unpulled(vault):
    """A shared graph's bookmark set by the other laptop, past anything this
    clone has pulled."""
    from artmind.manifest import read_vault_id

    vault.sync_bootstrap()
    vault.graph.bookmarks[read_vault_id(vault.root)] = "9" * 40
    return vault.run("vault", "sync", "--compact")


def s_vault_sync_refusal_bookmark_not_ancestor(vault):
    """The structured bookmark on a commit HEAD's history does not contain
    (history rewritten, or a diverged branch)."""
    from artmind.vault import VaultLayout, write_state

    vault.sync_bootstrap()
    vault.git("checkout", "-qb", "elsewhere")
    vault.write("Notes/elsewhere.md", "Elsewhere.\n")
    elsewhere = vault.commit("elsewhere")
    vault.git("checkout", "-q", "main")
    write_state(VaultLayout(vault.root), {"last_structured_commit": elsewhere})
    return vault.run("vault", "sync", "--compact")


def s_vault_sync_refusal_no_bookmark(vault):
    return vault.run("vault", "sync", "--compact")


def s_vault_resolve_dry_run(vault):
    """One unit resolved (a KG folder whose note merged cleanly), one pending
    (its note is still conflicted), one reported (same_as.yaml)."""
    from artmind.document_identity import compute_content_sha256

    def note(doc_id, body):
        return f"---\n_artmind_id: {doc_id}\n_domain: general\n---\n\n{body}"

    def folder(name, doc_id, body, obs):
        document = {"id": doc_id, "version": 2, "content_sha256": compute_content_sha256(body),
                    "source_path": f"Notes/{name}.md", "source_type": "md"}
        base = f".artmind/data/kg/general/{name}"
        return {f"{base}/document.json": json.dumps(document, indent=2), f"{base}/observations.json": obs,
                f"{base}/chunks.json": "[]", f"{base}/relationships.json": "[]"}

    vault.conflict(
        {"Notes/policy.md": note("id-policy", "Policy v1.\n"), **folder("policy", "id-policy", "Policy v1.\n", "[0]"),
         "Notes/memo.md": note("id-memo", "Memo v1.\n"), **folder("memo", "id-memo", "Memo v1.\n", "[0]"),
         ".artmind/same_as.yaml": "groups: []\n"},
        {**folder("policy", "id-policy", "Policy v1.\n", "[1]"),
         "Notes/memo.md": note("id-memo", "Memo, ours.\n"), **folder("memo", "id-memo", "Memo, ours.\n", "[1]"),
         ".artmind/same_as.yaml": "groups: [[a, b]]\n"},
        {"Notes/policy.md": note("id-policy", "Policy v2.\n"), **folder("policy", "id-policy", "Policy v2.\n", "[2]"),
         "Notes/memo.md": note("id-memo", "Memo, theirs.\n"), **folder("memo", "id-memo", "Memo, theirs.\n", "[2]"),
         ".artmind/same_as.yaml": "groups: [[c, d]]\n"},
    )
    return vault.run("vault", "resolve", "--dryRun", "--compact")


def s_vault_doctor(vault):
    vault.git("config", "pull.rebase", "true")
    return vault.run("vault", "doctor", "--compact")


def s_ingest_pending(vault):
    vault.write("Notes/new_idea.md", "A new idea.\n")
    vault.write("Team/org_chart.pdf", b"%PDF-1.4 org chart")
    vault.write("Team/team.csv", "name,employer,level\nAnn Lee,Acme,senior\n")
    vault.write("Drafts/unmapped.md", "Not mapped, never listed.\n")
    return vault.run("ingest", "pending", "--compact")


def s_ingest_async_pending(vault):
    vault.write("Notes/new_idea.md", "A new idea.\n")
    vault.write("Team/org_chart.pdf", b"%PDF-1.4 org chart")
    return vault.run("ingest", "async", "--pending", "--compact")


def s_ingest_async_nothing_pending(vault):
    import artmind.ingest as ing
    from conftest import stage_as_extracted

    result = ing.ingest_file(vault.root / "Notes/welcome.md", "image-model", "general")
    stage_as_extracted(ing.KG_DIR, result, "general")
    return vault.run("ingest", "async", "--pending", "--compact")


def _mid_job():
    return _jobs(
        [("completed", None), ("completed", None), ("processing", None), ("queued", None), ("queued", None)],
        status="processing", processed=2,
    )


def s_ingest_job_status_running(vault):
    job_id = _mid_job()
    with _hold_worker_lock(vault):
        return vault.run("ingest", "job-status", job_id, "--compact")


def s_ingest_job_status_stalled(vault):
    """`processing`, but no worker holds the lock: it died mid-job."""
    return vault.run("ingest", "job-status", _mid_job(), "--compact")


def s_ingest_retry_job_stalled(vault):
    """Re-queues the file the dead worker was on; the rest stay queued."""
    return vault.run("ingest", "retry-job", _mid_job(), "--compact")


def s_ingest_retry_job_failed(vault):
    job_id = _jobs(
        [("completed", None), ("failed", "KG ingestion failed")],
        status="failed", processed=2,
    )
    return vault.run("ingest", "retry-job", job_id, "--compact")


def s_ingest_job_status_done(vault):
    job_id = _jobs(
        [("completed", None), ("completed", None), ("completed", None), ("completed", None),
         ("failed", "KG ingestion failed")],
        status="failed", processed=5,
    )
    return vault.run("ingest", "job-status", job_id, "--compact")


def s_ingest_jobs_active(vault):
    """A job started elsewhere (a terminal, the admin-ui): the plugin finds
    it here and follows it."""
    _jobs([("completed", None), ("processing", None), ("queued", None)], status="processing", processed=1)
    with _hold_worker_lock(vault):
        return vault.run("ingest", "jobs-active", "--compact")


def s_ingest_job_results_done(vault):
    job_id = _jobs(
        [("completed", None), ("failed", "KG ingestion failed")],
        status="failed", processed=2,
    )
    return vault.run("ingest", "job-results", job_id, "--compact")


def s_table2graph_dry_run(vault):
    _table_registered_and_mapped(vault)
    return vault.run("ingest", "table2graph", "team", "--dryRun", "--compact")


def s_table2graph_dry_run_no_mapping(vault):
    from artmind.structured.pipeline import ingest_structured_file

    ingest_structured_file(vault.write("Team/budget.csv", "item,cost\nlaptops,1200\n"), "general")
    return vault.run("ingest", "table2graph", "budget", "--dryRun", "--compact")


def s_table2graph_pending(vault):
    _table_registered_and_mapped(vault)
    return vault.run("ingest", "table2graph", "--pending", "--compact")


SCENARIOS = {
    name[2:].replace("_", "-"): fn for name, fn in sorted(globals().items()) if name.startswith("s_") and callable(fn)
}

def fixture_name(scenario: str) -> str:
    """`vault-status-in-sync` -> `vault-status.in-sync`: the command, a dot,
    then the state."""
    for command in ("vault-status", "vault-sync", "vault-resolve", "vault-doctor", "ingest-pending",
                    "ingest-async", "ingest-job-status", "ingest-job-results", "ingest-jobs-active", "ingest-retry-job",
                    "table2graph"):
        if scenario == command:
            return command
        if scenario.startswith(command + "-"):
            return f"{command}.{scenario[len(command) + 1:]}"
    raise AssertionError(f"scenario {scenario!r} names no known command")


# ── normalisation ────────────────────────────────────────────────────────────

_SHA = re.compile(r"\b[0-9a-f]{40}\b")
_ABBREVIATED_SHA = re.compile(r"\b[0-9a-f]{12}\b")
_UUID = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b")
_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?")


def normalise(record: dict, vault: Vault) -> dict:
    """The same record from any run: see the module docstring."""
    import paths

    text = json.dumps(record, ensure_ascii=False)
    for real, fake in (
        (str(vault.root.resolve()), "/vault"),
        (str(vault.root), "/vault"),
        (str(Path(paths.ARTMIND_HOME).resolve()), "/artmind-home"),
        (str(paths.ARTMIND_HOME), "/artmind-home"),
        (str(Path.home().resolve()), "/home"),
        (str(Path.home()), "/home"),
    ):
        text = text.replace(real, fake)
    shas: dict[str, str] = {}
    for sha in _SHA.findall(text):
        shas.setdefault(sha, (format(len(shas) + 1, "x") * 40)[:40])
    for sha, fake in shas.items():
        text = text.replace(sha, fake).replace(sha[:12], fake[:12])
    # An abbreviation of a commit named nowhere in full (a refusal's own
    # message): same length, so a rich-click error box keeps its wrapping.
    for short in _ABBREVIATED_SHA.findall(text):
        if short not in {fake[:12] for fake in shas.values()}:
            shas.setdefault(short, (format(len(shas) + 1, "x") * 40)[:40])
            text = text.replace(short, shas[short][:12])
    uuids: dict[str, str] = {}
    for found in _UUID.findall(text):
        uuids.setdefault(found, f"00000000-0000-4000-8000-{len(uuids) + 1:012d}")
    for found, fake in uuids.items():
        text = text.replace(found, fake)
    text = _TIMESTAMP.sub("2026-09-30T12:00:00", text)
    return json.loads(text)


# ── the check ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("scenario", sorted(SCENARIOS))
def test_fixture_matches_the_cli(scenario, vault):
    record = normalise(SCENARIOS[scenario](vault), vault)
    path = FIXTURES / f"{fixture_name(scenario)}.json"
    if REGENERATE:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return
    assert path.is_file(), f"{path.name} is missing -- regenerate (see this module's docstring)"
    stored = json.loads(path.read_text(encoding="utf-8"))
    assert record == stored, (
        f"`artmind {' '.join(record['args'])}` no longer prints what {path.name} holds. If the change "
        "is intended, regenerate the fixtures and run the plugin's tests (this module's docstring)."
    )


def test_every_fixture_has_a_scenario():
    """A fixture no scenario regenerates would go stale unnoticed."""
    expected = {f"{fixture_name(s)}.json" for s in SCENARIOS}
    present = {p.name for p in FIXTURES.glob("*.json")}
    assert present == expected
