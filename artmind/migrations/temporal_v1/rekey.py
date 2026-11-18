"""Step `rekey` (plan Task 35; spec 2026-11-15 §5.2, §5.3, §8; questions 12-14):
travel OBJECTs become ACTIVITY, occasion classes re-key on their day, and the records
that name the old keys -- same-as groups, open same-as proposals, curation records --
follow, before one projection rebuild per domain.

Core computes the new keys: `retime.retime_observations` re-derives `_kind`, strips an
occasion's date from `canonical_name` and keys it on its day; observation ids never
change. This step adds only the one-time parts: the travel word list
(`reclassify.py`) and the rewrite of records that name old keys.

Judged by facts. Per domain, the plan is computed in memory: every staged folder's
observations through `reclassify` then `retime_observations`. An old key's *moves* are
the new keys of every observation that carried it, the old key read from this graph's
`:Observation` when it has one (so a re-run, or another machine that pulled the
re-keyed staging, still knows what each key was), else from staging. Pending:

- `objects_reclassified`, `entity_rekey` (old keys that move): staging still to write;
- `same_as_groups`, `curation_records`: files still naming moved keys (or a same-as
  group with a `category` member, which no review may fold);
- `sameas_proposals_closed`: open proposals `sameas.identity_refusal` refuses once
  their members are mapped through the moves (an old key with several days is a
  cross-day proposal);
- `documents_to_commit`: staged keys that differ from this graph's (a killed run, or
  staging pulled from a machine that migrated first);
- `records_to_apply`: record changes a killed run wrote but did not apply to this graph;
- `rebuild_pending`: a run that committed documents but died before its rebuild.

A run's manifest (`logs/migrate-temporal/<run_id>/<domain>/manifest.json`) holds its
moves, record changes and stage (`files` -> `graph` -> `rebuild` -> `done`). Until it is
done its moves join the plan, so a resume still knows the old keys of documents this
graph never held (staging already rewritten, nothing to compare with).

Apply, per domain: files first -- (1) staging, (2) `same_as.yaml`, (3) curation records
-- with a byte copy of every file under `logs/migrate-temporal/<run_id>/<domain>/`
(private, never pruned) taken before the first write; a failure in any of them
restores every file byte for byte and sends no graph write. Then the graph: open
proposals closed, record changes applied (`syntheses` moved to their new entity id,
`conflicts` dismissed; mart dimension records are files only), each document whose
staged keys differ from the graph's committed with the rebuild deferred, and one
`ingest.rebuild_projection(domain)`, the manifest at `rebuild` before the first commit
and `done` only after the rebuild. A failure there leaves the files written; the next
`migrate temporal` resumes from `documents_to_commit` / `records_to_apply` /
`rebuild_pending`.

A same-as group or an open proposal is in scope for a domain when any member is in
it (its canonical may sit in another domain); only that domain's moving members are
rewritten, and a rewritten group `sameas.identity_refusal` refuses is dropped.
`same_as.yaml` is re-serialised when it changes: its comments are not kept.

The files travel (commit them); the graph changes are this machine's. Another machine
that pulls the re-keyed staging runs `migrate temporal` too: it commits and rebuilds,
and closes its own refused proposals; record changes reach its graph with `vault sync`.
"""
from __future__ import annotations

import json
import os
import shutil
import stat
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from artmind.migrations.temporal_v1.steps import MigrationContext

STEP = "rekey"
LOG_FOLDER = "migrate-temporal"
#: Documents per graph read of their observations' keys.
DOC_BATCH = 500
SYNTHESES, CONFLICTS, MART_DIMENSION = "syntheses", "conflicts", "mart_dimension"
#: Conflict statuses a re-key closes (a resolved or dismissed one keeps its outcome).
OPEN = "open"

#: Overridable in tests: the schema an entity of a domain was keyed under (family merged).
schema_for = None


def _schema_for(domain: str) -> dict:
    from artmind import sameas

    return (schema_for or sameas._schema_for)(domain)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _key(value) -> str | None:
    """A stored key string, normalised as `same_as.yaml` reads it, or None when malformed."""
    from artmind.observations import key_string, normalize_identity

    parts = str(value or "").strip().split("|")
    if len(parts) != 3:
        return None
    return key_string((normalize_identity(parts[0]), parts[1].strip(), parts[2].strip()))


def _tuple(key: str) -> tuple[str, str, str]:
    a, b, c = key.split("|")
    return a, b, c


def _in(domain: str | None, scope: str | None) -> bool:
    return scope is None or domain == scope


# ── moves ─────────────────────────────────────────────────────────────────────

def _moves(pairs) -> dict[str, set[str]]:
    """{old key: every new key of the observations that carried it}, for the old keys at
    least one of whose observations moved. `pairs` are (old key, new key) per observation."""
    by_old: dict[str, set[str]] = {}
    moved: set[str] = set()
    for old, new in pairs:
        if not old or not new:
            continue
        by_old.setdefault(old, set()).add(new)
        if old != new:
            moved.add(old)
    return {k: v for k, v in by_old.items() if k in moved}


def key_moves(before: list[dict], after: list[dict]) -> dict[str, set[str]]:
    """The moves between two versions of the same observations, compared by id."""
    old = {o["id"]: o.get("key") for o in before if isinstance(o, dict) and o.get("id")}
    return _moves((old.get(o.get("id")), o.get("key")) for o in after if isinstance(o, dict) and o.get("id"))


def _day(key: str) -> str:
    from artmind.observations import split_occasion

    return split_occasion(key.split("|")[0])[1] or ""


# ── the plan ──────────────────────────────────────────────────────────────────

def _dump(observations: list[dict]) -> bytes:
    """`ingest.write_staging`'s byte form of a staging JSON file."""
    return json.dumps(observations, indent=2, ensure_ascii=False).encode("utf-8")


@dataclass
class FolderPlan:
    folder: Path
    doc_id: str | None
    #: {observation id: key} as staged (the staged list itself is not kept).
    staged_keys: dict
    planned: list[dict]
    dropped: dict
    moved: Counter
    changed: bool

    @staticmethod
    def build(folder: Path, doc_id, staged: list[dict], planned: list[dict], dropped: dict,
              moved: Counter) -> "FolderPlan":
        def shape(observations):
            return [(o.get("id"), o.get("key"), o.get("entity_class")) for o in observations if isinstance(o, dict)]

        keys = {o.get("id"): o.get("key") for o in staged if isinstance(o, dict) and o.get("id")}
        return FolderPlan(folder, doc_id, keys, planned, dropped, moved,
                          bool(moved) or shape(staged) != shape(planned))

    @property
    def path(self) -> Path:
        return self.folder / "observations.json"

    @property
    def data(self) -> bytes:
        return _dump(self.planned)


@dataclass
class DomainPlan:
    domain: str
    schema: dict
    folders: list[FolderPlan]
    #: {observation id: key} of this graph's current observations of the staged documents;
    #: None when the graph was not read.
    graph: dict[str, str] | None = None
    moves: dict[str, set[str]] = field(default_factory=dict)
    counts: Counter = field(default_factory=Counter)
    #: `_file_writes` memoised against a stamp of same_as.yaml and the record folders.
    _writes: tuple | None = None

    @property
    def changed(self) -> list[FolderPlan]:
        return [f for f in self.folders if f.changed]

    def to_commit(self) -> list[FolderPlan]:
        """Folders whose planned keys differ from this graph's (a document not in the
        graph is not this step's to commit)."""
        if not self.graph:
            return []
        out = []
        for f in self.folders:
            shared = [(o["id"], o.get("key")) for o in f.planned if isinstance(o, dict) and o.get("id") in self.graph]
            if any(self.graph[i] != k for i, k in shared):
                out.append(f)
        return out


def _load_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _graph_keys(session, doc_ids: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for i in range(0, len(doc_ids), DOC_BATCH):
        rows = session.run(
            "UNWIND $ids AS doc_id MATCH (o:Observation {doc_id: doc_id}) WHERE o.key IS NOT NULL "
            "RETURN o.id AS id, o.key AS key", ids=doc_ids[i:i + DOC_BATCH]).data()
        out.update({r["id"]: r["key"] for r in rows if isinstance(r, dict) and r.get("id") and r.get("key")})
    return out


def chat_observation(obs: dict, schema: dict) -> dict:
    """An `artmind update` observation (`USER_CHAT_SOURCE_KIND`) as this model keys it.

    `update confirm` keys a link on the node the user picked (`identity=` its stored key),
    and dates the folder by the chat, so retime must never re-derive it. Only an old one
    of an occasion class whose key has no day gains the day: its stored name part plus
    `occasion_day` of its own `_valid_from`; everything else stays as it is."""
    from artmind.observations import OCCASION_SEP, key_string, occasion_day, split_occasion
    from artmind.schema_validate import class_identity

    key = obs.get("key") if isinstance(obs, dict) else None
    parts = str(key or "").split("|")
    if len(parts) != 3 or class_identity(schema, obs.get("entity_class") or parts[1]) != "occasion":
        return obs
    name, day = split_occasion(parts[0])
    new_day = occasion_day(obs.get("_valid_from"), obs.get("_valid_precision"))
    if day or not new_day or not name:
        return obs
    return {**obs, "key": key_string((f"{name}{OCCASION_SEP}{new_day}", parts[1], parts[2]))}


#: Plans memoised for one process (one `migrate` invocation): keyed by domain, whether
#: the graph was read, and a stamp of the staging and schema files; `apply` clears it.
_PLANS: dict[tuple, DomainPlan | None] = {}


def _stamp(domain: str) -> tuple:
    from artmind import ingest, retime

    schema = Path(ingest.DOMAIN_SCHEMAS_DIR) / f"{domain}_schema.yaml"
    out = [str(ingest.KG_DIR), str(schema), schema.stat().st_mtime_ns if schema.is_file() else None]
    for folder in retime.staging_folders(domain):
        try:
            st = (folder / "observations.json").stat()
            out.append((folder.name, st.st_mtime_ns, st.st_size))
        except OSError:
            out.append((folder.name, None, None))
    return tuple(out)


def clear_plans() -> None:
    _PLANS.clear()


def plan_domain(domain: str, session=None) -> DomainPlan | None:
    """The domain's plan, read from staging (and this graph's keys when `session` is
    given); None when the domain has no schema file. Memoised per process (`_PLANS`)."""
    cache_key = (domain, session is not None, _stamp(domain))
    if cache_key not in _PLANS:
        _PLANS[cache_key] = _plan_domain(domain, session)
    return _PLANS[cache_key]


def _plan_domain(domain: str, session=None) -> DomainPlan | None:
    from artmind import ingest, retime
    from artmind.migrations.temporal_v1.reclassify import reclassify
    from artmind.temporal import load_schema

    if not (Path(ingest.DOMAIN_SCHEMAS_DIR) / f"{domain}_schema.yaml").is_file():
        return None
    schema = load_schema(domain, schemas_dir=ingest.DOMAIN_SCHEMAS_DIR)
    folders: list[FolderPlan] = []
    for folder in retime.staging_folders(domain):
        document = _load_json(folder / "document.json")
        staged = _load_json(folder / "observations.json", None)
        if not isinstance(document, dict) or not isinstance(staged, list) or document.get("source_kind") == "table":
            continue  # a table folder keeps its mapping's keys (plan question 21)
        if document.get("source_kind") == ingest.USER_CHAT_SOURCE_KIND:
            planned = [chat_observation(o, schema) for o in staged]
            folders.append(FolderPlan.build(folder, document.get("id"), staged, planned, {}, Counter()))
            continue
        moved_in, moved = reclassify(staged, schema)
        planned, dropped = retime.retime_observations(moved_in, document, schema)
        folders.append(FolderPlan.build(folder, document.get("id"), staged, planned, dropped, moved))
    plan = DomainPlan(domain, schema, folders)
    if session is not None:
        plan.graph = _graph_keys(session, sorted({f.doc_id for f in folders if f.doc_id}))
    graph = plan.graph or {}
    pairs = []
    for f in folders:
        for o in f.planned:
            if isinstance(o, dict) and o.get("id"):
                pairs.append((graph.get(o["id"]) or f.staged_keys.get(o["id"]), o.get("key")))
                plan.counts[o.get("key")] += 1
    plan.moves = _moves(pairs)
    # An unfinished run's moves: the old keys of documents this graph never held.
    for manifest in unfinished(domain):
        for old, new in (manifest.get("moves") or {}).items():
            plan.moves[old] = plan.moves.get(old, set()) | set(new)
    return plan


# ── same_as.yaml ──────────────────────────────────────────────────────────────

def _same_as_path() -> Path:
    from artmind import same_as

    return Path(same_as.SAME_AS_PATH)


def _same_as_bytes(raw: dict) -> bytes:
    """`same_as.save_groups`' byte form."""
    import yaml

    return yaml.safe_dump(raw, sort_keys=False, allow_unicode=True).encode("utf-8")


def _plan_same_as(text: str | None, moves: dict[str, set[str]], schema_for_, domain: str | None):
    """(new file payload or None when nothing changes, report)."""
    import yaml

    from artmind.schema_validate import class_identity

    from artmind.sameas import identity_refusal

    report = {"groups_before": 0, "groups_after": 0, "rekeyed": 0, "split": 0, "emptied": 0,
              "dropped_category": 0, "members_dropped": 0, "refused": 0,
              "canonical_not_member": 0, "key_in_two_groups": 0}
    try:
        raw = yaml.safe_load(text) if text else None
    except yaml.YAMLError:
        raw = None
    if not isinstance(raw, dict) or not isinstance(raw.get("groups"), list):
        return None, report
    groups = raw["groups"]
    report["groups_before"] = len(groups)
    out, changed = [], False
    seen: set[str] = set()
    for entry in groups:
        members = [_key(m) for m in (entry or {}).get("members") or []] if isinstance(entry, dict) else []
        canonical = _key(entry.get("canonical")) if isinstance(entry, dict) else None
        if canonical and members and canonical not in members:
            report["canonical_not_member"] += 1
            logger.warning("migrate rekey: a same_as.yaml group's canonical is not among its members; left as is")
        if any(m in seen for m in members if m):
            report["key_in_two_groups"] += 1
            logger.warning("migrate rekey: a same_as.yaml key is in two groups (the loader keeps the first)")
        seen |= {m for m in members if m}
        if not canonical or not members or None in members or canonical not in members \
                or not any(_in(m.split("|")[2], domain) for m in members):
            out.append(entry)  # malformed, or no member in this domain: not this step's to judge
            continue
        if any(class_identity(schema_for_(m.split("|")[2]) or {}, m.split("|")[1]) == "category" for m in members):
            report["dropped_category"] += 1
            changed = True
            continue
        if not any(m in moves for m in members):
            out.append(entry)
            continue
        report["rekeyed"] += 1
        changed = True
        by_day: dict[str, set[str]] = {}
        for m in members:
            for new in moves.get(m, {m}):
                by_day.setdefault(_day(new), set()).add(new)
        canon_new = {_day(k): k for k in sorted(moves.get(canonical, {canonical}))}
        kept = 0
        for day, day_members in sorted(by_day.items()):
            if len(day_members) < 2:
                report["members_dropped"] += len(day_members)
                continue
            canon = canon_new.get(day) if canon_new.get(day) in day_members else sorted(day_members)[0]
            rest = sorted(day_members - {canon})
            if identity_refusal(_tuple(canon), [_tuple(m) for m in rest], schema_for=schema_for_):
                report["refused"] += 1
                continue
            out.append({"canonical": canon, "members": [canon] + rest})
            kept += 1
        report["split"] += 1 if kept > 1 else 0
        report["emptied"] += 1 if kept == 0 else 0
    report["groups_after"] = len(out)
    return ({**raw, "groups": out} if changed else None), report


def migrate_same_as(path: Path, moves: dict[str, set[str]], schema_for_=None, *, write: bool,
                    domain: str | None = None) -> tuple[list[dict], dict]:
    """Rewrite `same_as.yaml`'s groups for re-keyed members (question 12): each occasion
    member expands into its new day keys; one group per day on which at least two members
    have observations, canonical = the old canonical's key for that day; a member with no
    same-day partner is dropped; a group with a `category` member, or one
    `identity_refusal` refuses, is dropped. A group is in scope when any member is in
    `domain`; others, and malformed ones, are left as they are. Returns (the
    file's groups, report); writes atomically only with `write` and a change."""
    path = Path(path)
    text = path.read_text(encoding="utf-8") if path.is_file() else None
    payload, report = _plan_same_as(text, moves, schema_for_ or _schema_for, domain)
    if payload is None:
        try:
            import yaml

            current = (yaml.safe_load(text) or {}).get("groups") if text else []
        except Exception:  # noqa: BLE001 - an unreadable file is left alone
            current = []
        return list(current or []), report
    if write:
        _write_bytes(path, _same_as_bytes(payload))
    return payload["groups"], report


def _write_bytes(path: Path, data: bytes) -> None:
    """Atomic: a sibling temp file, then `os.replace`."""
    import tempfile

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".artmind-tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        if path.exists():
            os.chmod(tmp, path.stat().st_mode & 0o7777)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


# ── open same-as proposals ────────────────────────────────────────────────────

_OPEN_PROPOSALS = ("MATCH (p:SameAsProposal) WHERE p.status = 'open' "
                   "RETURN p.id AS id, p.canonical AS canonical, p.members AS members")
_CLOSE = ("UNWIND $rows AS row MATCH (p:SameAsProposal {id: row.id}) WHERE p.status = 'open' "
          "SET p.status = 'rejected', p.resolved_at = $now, p.resolution_reason = row.reason")


def refused_proposals(session, moves: dict[str, set[str]], schema_for_=None,
                      domain: str | None = None) -> list[dict]:
    """[{id, reason}] for the open proposals with a member in `domain` that `sameas.identity_refusal`
    refuses once every member is mapped through `moves` (an old key with several days
    makes the proposal cross-day: `identity:occasion`)."""
    from artmind.sameas import identity_refusal

    load = schema_for_ or _schema_for
    rows = []
    for row in session.run(_OPEN_PROPOSALS).data():
        if not isinstance(row, dict) or not row.get("id"):
            continue
        canonical = _key(row.get("canonical"))
        members = [k for m in row.get("members") or [] if (k := _key(m))]
        if not canonical or not any(_in(k.split("|")[2], domain) for k in [canonical, *members]):
            continue
        mapped_canonical = sorted(moves.get(canonical, {canonical}))
        mapped = sorted({n for m in [canonical, *members] for n in moves.get(m, {m})})
        refusal = identity_refusal(_tuple(mapped_canonical[0]), [_tuple(m) for m in mapped], schema_for=load)
        if refusal:
            rows.append({"id": row["id"], "reason": refusal.split(" ", 1)[0]})
    return rows


def close_proposals(session, moves: dict[str, set[str]], schema_for_=None, *, dry_run: bool,
                    domain: str | None = None) -> int:
    """Reject every refused open proposal (`refused_proposals`), recording the reason;
    a dry run only counts them."""
    rows = refused_proposals(session, moves, schema_for_, domain)
    if rows and not dry_run:
        session.run(_CLOSE, rows=rows, now=_now())
    return len(rows)


# ── curation records ──────────────────────────────────────────────────────────

@dataclass
class RecordChange:
    kind: str
    old: dict
    #: The records to write (a conflict keeps its id; the others take new ids).
    new: list[dict]
    delete_old: bool

    def writes(self) -> list[tuple[Path, bytes | None]]:
        """(path, bytes to write, or None for a delete)."""
        from artmind import curation_records

        out = [(curation_records.record_path(self.kind, r["id"]), curation_records.serialize(r)) for r in self.new]
        if self.delete_old:
            out.append((curation_records.record_path(self.kind, self.old["id"]), None))
        return out


def _records(kind: str) -> list[dict]:
    from artmind import curation_records

    folder = curation_records.curation_dir() / kind
    out = []
    for path in sorted(folder.glob("*.json")) if folder.is_dir() else []:
        record = curation_records.parse(path.read_bytes())
        if isinstance(record, dict) and isinstance(record.get("id"), str) and path.stem == record["id"]:
            out.append(record)
    return out


def plan_records(moves: dict[str, set[str]], counts: Counter, domain: str | None = None) -> list[RecordChange]:
    """The curation records that name a moved key (spec §8):

    - `syntheses`: re-pointed to the new key with the most observations (`counts`; ties
      by key), under that key's entity id;
    - `mart_dimension`: one record per new key (the decision held for every day);
    - `conflicts`: an open conflict with a moved side is dismissed (`identity:occasion`):
      its sides are no longer the entities it compared.
    Supersession, lifecycle and mart calendar records name documents and periods, not
    entity keys."""
    from artmind.mart import records as mart_records
    from artmind.observations import entity_id

    changes: list[RecordChange] = []
    for record in _records(SYNTHESES):
        key = _key(record.get("key"))
        if key in moves and _in(key.split("|")[2], domain):
            new_key = min(moves[key], key=lambda k: (-counts.get(k, 0), k))
            changes.append(RecordChange(SYNTHESES, record, [{**record, "id": entity_id(_tuple(new_key)),
                                                             "key": new_key}], True))
    for record in _records(MART_DIMENSION):
        key = _key(record.get("member_key"))
        if key in moves and _in(record.get("domain"), domain):
            new = [{**record, "member_key": k,
                    "id": mart_records._id(record["domain"], record["dimension"], k, record["source"])}
                   for k in sorted(moves[key])]
            changes.append(RecordChange(MART_DIMENSION, record, new, True))
    now = None
    for record in _records(CONFLICTS):
        keys = [_key(e.get("key")) for e in record.get("entities") or [] if isinstance(e, dict)]
        domains = [d for d in record.get("domains") or [] if isinstance(d, str)]
        if record.get("status", OPEN) == OPEN and any(k in moves for k in keys) \
                and (domain is None or domain in domains or any(k and k.split("|")[2] == domain for k in keys)):
            now = now or _now()
            changes.append(RecordChange(CONFLICTS, record, [{**record, "status": "dismissed",
                                                             "resolution_reason": "identity:occasion",
                                                             "resolved_at": now}], False))
    return changes


def write_records(changes: list[RecordChange]) -> None:
    """New files first, then the old ones removed, so a kill never loses a decision."""
    from artmind import curation_records

    for change in changes:
        for record in change.new:
            curation_records.write_record(change.kind, record)
    for change in changes:
        if change.delete_old:
            curation_records.delete_record(change.kind, change.old["id"])


def migrate_records(moves: dict[str, set[str]], counts: Counter, *, write: bool,
                    domain: str | None = None) -> tuple[list[RecordChange], dict]:
    changes = plan_records(moves, counts, domain)
    if write:
        write_records(changes)
    return changes, dict(Counter(c.kind for c in changes))


# ── recovery copies ───────────────────────────────────────────────────────────

class Stash:
    """`logs/migrate-temporal/<run_id>/<domain>/`: a byte copy (and mode) of every file the
    run writes, taken before the first write, and the manifest a resumed run reads."""

    def __init__(self, run_id: str, domain: str):
        from artmind import ingest

        self.root = Path(ingest.LOGS_DIR) / LOG_FOLDER / run_id / domain
        self.manifest = {"run_id": run_id, "domain": domain, "stage": "files", "files": [], "records": [],
                         "moves": {}}
        self.saved = False

    def _save(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        _write_bytes(self.root / "manifest.json",
                     json.dumps(self.manifest, indent=2, ensure_ascii=False, default=sorted).encode("utf-8"))
        self.saved = True

    def keep(self, paths: list[Path]) -> None:
        copies = self.root / "files"
        copies.mkdir(parents=True, exist_ok=True)
        for i, path in enumerate(dict.fromkeys(Path(p) for p in paths)):
            entry = {"path": str(path), "copy": None, "mode": None}
            if path.is_file():
                entry["copy"] = f"{i:06d}"
                entry["mode"] = stat.S_IMODE(path.stat().st_mode)
                shutil.copyfile(path, copies / entry["copy"])
            self.manifest["files"].append(entry)
        self._save()

    def restore(self) -> None:
        """Every kept file back byte for byte, with its mode; a file the run created is removed."""
        from artmind.atomic_dir import write_dir_atomic

        for entry in reversed(self.manifest["files"]):
            path = Path(entry["path"])
            if entry["copy"] is None:
                path.unlink(missing_ok=True)
                continue
            data = (self.root / "files" / entry["copy"]).read_bytes()
            if path.name == "observations.json" and (path.parent / "document.json").exists():
                write_dir_atomic(path.parent, {path.name: data})  # a staging folder swaps whole
            else:
                _write_bytes(path, data)
            if entry.get("mode") is not None:
                os.chmod(path, entry["mode"])
        self.stage("restored")

    def stage(self, name: str) -> None:
        self.manifest["stage"] = name
        self._save()


#: Stages of a run's manifest still to finish (`restored`, `restore_failed`, `done` are not).
UNFINISHED = ("files", "graph", "rebuild")


def unfinished(domain: str) -> list[dict]:
    """Manifests of this domain's runs not yet done on this graph."""
    from artmind import ingest

    root = Path(ingest.LOGS_DIR) / LOG_FOLDER
    out = []
    for path in sorted(root.glob(f"*/{domain}/manifest.json")) if root.is_dir() else []:
        manifest = _load_json(path, {})
        if isinstance(manifest, dict) and manifest.get("stage") in UNFINISHED:
            out.append({**manifest, "_path": str(path)})
    return out


# ── the step ──────────────────────────────────────────────────────────────────

def _domains(ctx: MigrationContext) -> list[str]:
    from artmind.migrations.temporal_v1.cli import _all_domains

    names = _all_domains()
    if ctx.domains is None:
        return names
    return [n for n in names if any(n == d or n.startswith(d + ".") for d in ctx.domains)]


def _files_stamp() -> tuple:
    from artmind import curation_records

    out = []
    for path in [_same_as_path()] + [curation_records.curation_dir() / k for k in (SYNTHESES, CONFLICTS, MART_DIMENSION)]:
        try:
            st = path.stat()
            out.append((str(path), st.st_mtime_ns, st.st_size))
        except OSError:
            out.append((str(path), None, None))
    return tuple(out)


def _file_writes(plan: DomainPlan) -> tuple[list[tuple[Path, bytes | None]], bytes | None, list[RecordChange], dict]:
    """(every (path, bytes) the domain's file stage writes, same_as bytes, record changes,
    same-as report), memoised on the plan until same_as.yaml or a record folder changes."""
    stamp = _files_stamp()
    if plan._writes is None or plan._writes[0] != stamp:
        plan._writes = (stamp, _compute_file_writes(plan))
    return plan._writes[1]


def _compute_file_writes(plan: DomainPlan):
    path = _same_as_path()
    text = path.read_text(encoding="utf-8") if path.is_file() else None
    payload, report = _plan_same_as(text, plan.moves, _schema_for, plan.domain)
    same_as_data = _same_as_bytes(payload) if payload is not None else None
    changes = plan_records(plan.moves, plan.counts, plan.domain)
    writes = [(f.path, f.data) for f in plan.changed]
    if same_as_data is not None:
        writes.append((path, same_as_data))
    for change in changes:
        writes.extend(change.writes())
    return writes, same_as_data, changes, report


def _display(path: Path) -> str:
    import paths

    try:
        return str(Path(path).resolve().relative_to(Path(paths.ARTMIND_VAULT_DIR).resolve()))
    except (TypeError, ValueError):
        return str(path)


def _same_as_count(report: dict) -> int:
    """Groups a run rewrites or drops (the same number in pending and apply)."""
    return report["rekeyed"] + report["dropped_category"]


def _domain_counts(plan: DomainPlan, session) -> tuple[dict, list[str]]:
    writes, same_as_data, changes, report = _file_writes(plan)
    changed = {f.folder for f in plan.changed}
    counts = {
        "objects_reclassified": sum(sum(f.moved.values()) for f in plan.changed),
        "entity_rekey": len(plan.moves),
        "same_as_groups": _same_as_count(report) if same_as_data is not None else 0,
        "sameas_proposals_closed": len(refused_proposals(session, plan.moves, None, plan.domain)),
        "curation_records": len(changes),
        "documents_to_commit": len([f for f in plan.to_commit() if f.folder not in changed]),
        "records_to_apply": sum(len(m.get("records") or []) for m in unfinished(plan.domain)
                                if m.get("stage") in ("files", "graph"))
        + len(pulled_conflicts(session, plan)),
        "rebuild_pending": sum(1 for m in unfinished(plan.domain) if m.get("stage") == "rebuild"),
    }
    files = [_display(p) for p, _ in writes if p.name != "observations.json"]
    if changed:
        files.insert(0, _display(Path(plan.changed[0].folder).parent))
    return {k: v for k, v in counts.items() if v}, files


COUNT_KEYS = ("objects_reclassified", "entity_rekey", "same_as_groups", "sameas_proposals_closed",
              "curation_records", "documents_to_commit", "records_to_apply", "rebuild_pending")


def pending(ctx: MigrationContext) -> dict:
    """Totals over the domains in scope (empty = done), plus `by_domain`, `classes` (the
    classes whose keys move) and `files` (what the run writes), which inform only."""
    totals: Counter = Counter()
    by_domain, classes, files = {}, set(), []
    with ctx.session_factory() as session:
        for domain in _domains(ctx):
            plan = plan_domain(domain, session)
            if plan is None:
                continue
            counts, domain_files = _domain_counts(plan, session)
            if counts:
                by_domain[domain] = counts
                totals.update(counts)
                classes |= {k.split("|")[1] for k in plan.moves}
                files.extend(domain_files)
    if not totals:
        return {}
    return {**{k: totals[k] for k in COUNT_KEYS if totals[k]}, "by_domain": by_domain,
            "classes": sorted(classes), "files": sorted(dict.fromkeys(files))}


def _plans_best_effort(ctx: MigrationContext) -> list[DomainPlan]:
    """Plans with this graph's keys when it can be read, else from staging alone (an
    unreachable graph is its own blocker)."""
    try:
        with ctx.session_factory() as session:
            return [p for d in _domains(ctx) if (p := plan_domain(d, session))]
    except Exception:  # noqa: BLE001
        return [p for d in _domains(ctx) if (p := plan_domain(d))]


def writes(ctx: MigrationContext) -> list[Path]:
    """The exact files the run writes or removes (the run requires them git-clean)."""
    return [p for plan in _plans_best_effort(ctx) for p, _ in _file_writes(plan)[0]]


def own_work(ctx: MigrationContext) -> list[Path]:
    """Declared files already holding exactly what the run would write: a killed run's."""
    return [p for plan in _plans_best_effort(ctx) for p, data in _file_writes(plan)[0]
            if data is not None and p.is_file() and p.read_bytes() == data]


def effect(pending_counts: dict) -> dict:
    """The step's part of `migrate temporal`'s effect."""
    from artmind.cli_conventions import Change

    classes = list(pending_counts.get("classes") or [])
    changes = [Change(kind, pending_counts[kind], classes if kind == "entity_rekey" else [])
               for kind in ("objects_reclassified", "entity_rekey", "same_as_groups", "sameas_proposals_closed",
                            "curation_records") if pending_counts.get(kind)]
    return {
        "changes": changes,
        "records": list(pending_counts.get("files") or []),
        "undo": ("rekey: git restore the listed files and delete the record files the run created (byte copies "
                 "of every file it wrote are under logs/migrate-temporal/<run_id>/), then commit the restored "
                 "staging as it is with `artmind ingest write-to-graph --folder .artmind/data/kg/<domain>` "
                 "(not `ingest retime`, which re-keys again). These stay changed on this graph: closed same-as "
                 "proposals, dismissed conflicts and synthesis nodes moved to their new entity ids. "
                 "same_as.yaml is re-serialised by the run: its comments are not kept"),
    }


def _apply_records_to_graph(session, domain: str, records: list[dict]) -> int:
    """Each record change on this graph: a synthesis node moves to its new entity id, a
    conflict takes its new status. Idempotent; mart dimension records are files only."""
    from artmind import conflict_records, curation_records, synthesis_records
    from artmind.catalogue import state as catalogue_state

    done = 0
    for change in records:
        kind = change["kind"]
        if kind not in (SYNTHESES, CONFLICTS):
            continue

        def write(tx, change=change, kind=kind):
            if kind == SYNTHESES:
                synthesis_records.remove(tx, change["old"])
                catalogue_state.bump(tx, [domain])
            for rid in change["new_ids"]:
                path = curation_records.record_path(kind, rid)
                if not path.is_file():
                    logger.warning("migrate rekey: the {} record {} is missing; not applied", kind, rid)
                    continue
                data = path.read_bytes()
                module = synthesis_records if kind == SYNTHESES else conflict_records
                module.apply(tx, curation_records.parse(data), curation_records.fingerprint(data))

        session.execute_write(write)
        done += 1
    return done


def pulled_conflicts(session, plan: DomainPlan) -> list[dict]:
    """Conflict records another machine's run dismissed (`identity:occasion`) that name a
    key moving here and that this graph has not applied (its fingerprint differs): a
    machine with no run manifest still closes them before its commits remove the entities."""
    from artmind import conflict_records, curation_records

    if not plan.moves:
        return []
    candidates = []
    for record in _records(CONFLICTS):
        keys = [_key(e.get("key")) for e in record.get("entities") or [] if isinstance(e, dict)]
        if record.get("resolution_reason") == "identity:occasion" and any(k in plan.moves for k in keys) \
                and any(k and k.split("|")[2] == plan.domain for k in keys):
            candidates.append(record)
    if not candidates:
        return []
    held = {r["id"]: r.get("fingerprint") for r in session.run(
        conflict_records._READ_FINGERPRINTS, ids=sorted(r["id"] for r in candidates)).data() if isinstance(r, dict)}
    return [r for r in candidates
            if held.get(r["id"]) != curation_records.fingerprint(
                curation_records.record_path(CONFLICTS, r["id"]).read_bytes())]


def _graph_stage(ctx: MigrationContext, plan: DomainPlan, stash: Stash) -> dict:
    """Proposals closed, unfinished record changes applied, documents committed, one
    rebuild. Every unfinished manifest of the domain (this run's `stash` too) is at
    `rebuild` before the first commit and `done` only after the rebuild, so a kill in
    between leaves `rebuild_pending`."""
    from artmind import ingest

    out: Counter = Counter()
    earlier = [m for m in unfinished(plan.domain) if m.get("run_id") != stash.manifest["run_id"]]
    runs = [(Path(m["_path"]), m) for m in earlier]
    with ctx.session_factory() as session:
        out["sameas_proposals_closed"] = close_proposals(session, plan.moves, None, dry_run=False,
                                                         domain=plan.domain)
        for manifest in [*earlier, *([stash.manifest] if stash.saved else [])]:
            if manifest.get("stage") in ("files", "graph"):
                out["records_applied"] += _apply_records_to_graph(session, plan.domain, manifest.get("records") or [])
        pulled = pulled_conflicts(session, plan)
        out["records_applied"] += _apply_records_to_graph(
            session, plan.domain, [{"kind": CONFLICTS, "new_ids": [r["id"]]} for r in pulled])
    to_commit = plan.to_commit()
    rebuild = bool(to_commit or out["records_applied"] or any(m.get("stage") == "rebuild" for m in earlier))
    if rebuild:
        for path, manifest in runs:
            _mark(path, manifest, "rebuild")
        stash.stage("rebuild")
    failed, committed = [], 0
    try:
        for f in to_commit:
            try:
                ok = ingest.commit_to_graph(f.folder, plan.domain, defer_rebuild=True)
            except Exception as exc:  # noqa: BLE001 - every folder is tried; the run reports them
                logger.error("migrate rekey: commit of {} failed: {}", f.folder.name, exc)
                ok = False
            if ok:
                committed += 1
            else:
                failed.append(f.folder.name)
    finally:
        if rebuild:
            ingest.rebuild_projection(plan.domain)
    # Reached only when the rebuild (if any) returned.
    for path, manifest in runs:
        _mark(path, manifest, "done")
    if stash.saved:
        stash.stage("done")
    out["documents_committed"] = committed
    out["rebuilt"] = 1 if rebuild else 0
    if failed:
        raise RuntimeError(f"rekey: {len(failed)} document(s) of {plan.domain} were not committed; "
                           f"the staging is written: run `artmind migrate temporal` again to resume")
    return {k: v for k, v in out.items() if v}


def _mark(path: Path, manifest: dict, stage: str) -> None:
    body = {k: v for k, v in manifest.items() if k != "_path"}
    _write_bytes(path, json.dumps({**body, "stage": stage}, indent=2, ensure_ascii=False).encode("utf-8"))


def _file_stage(plan: DomainPlan, stash: Stash) -> Counter:
    """Staging, `same_as.yaml`, curation records; every file restored on any failure."""
    from artmind import ingest, retime

    writes_, same_as_data, changes, report = _file_writes(plan)
    if not writes_:
        return Counter()
    # Moves and record changes are in the manifest before the first write: the run that
    # resumes a killed one still knows the old keys and applies its records.
    stash.manifest["records"] = [{"kind": c.kind, "old": c.old, "new_ids": [r["id"] for r in c.new]}
                                 for c in changes]
    stash.manifest["moves"] = {k: sorted(v) for k, v in plan.moves.items()}
    stash.keep([p for p, _ in writes_])
    try:
        for f in plan.changed:
            if f.dropped:
                retime._keep_dropped(f.folder, f.folder.name, f.dropped)
            ingest.write_staging(f.folder, {"observations.json": f.planned})
        if same_as_data is not None:
            _write_bytes(_same_as_path(), same_as_data)
        write_records(changes)
    except BaseException:
        try:
            stash.restore()
        except BaseException as restore_error:  # noqa: BLE001 - the original error is the one raised
            logger.error("migrate rekey: restoring {} failed ({}); copies are under {}",
                         plan.domain, restore_error, stash.root)
            try:
                stash.stage("restore_failed")
            except BaseException:  # noqa: BLE001
                pass
        raise
    stash.stage("graph")
    return Counter({
        "objects_reclassified": sum(sum(f.moved.values()) for f in plan.changed),
        "documents_written": len(plan.changed),
        "entity_rekey": len(plan.moves),
        "same_as_groups": _same_as_count(report) if same_as_data is not None else 0,
        "curation_records": len(changes),
    })


def apply(ctx: MigrationContext, dry_run: bool = False) -> dict:
    """Per domain: the file stage (restored byte for byte on any failure, no graph write
    before it ends), then the graph stage (resumable). Stops at the first failure."""
    if dry_run:
        return {}
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    totals: Counter = Counter()
    try:
        for domain in _domains(ctx):
            with ctx.session_factory() as session:
                plan = plan_domain(domain, session)
            if plan is None:
                continue
            stash = Stash(run_id, domain)
            totals.update(_file_stage(plan, stash))
            totals.update(_graph_stage(ctx, plan, stash))
            clear_plans()  # this domain's files changed: the next domain plans afresh
    finally:
        clear_plans()  # the graph and staging changed: the re-check plans afresh
    return {k: v for k, v in totals.items() if v}
