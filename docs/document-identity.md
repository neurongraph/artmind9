# Document identity and versioning

The Phase 2 specification. Vocabulary in [CONTEXT.md](../CONTEXT.md); stores in
[stores-and-repos.md](./stores-and-repos.md).

## Identity is assigned, never derived

Every attribute you could derive an identity from is mutable:

| Attribute | Changes when |
|---|---|
| path | `git mv`, reorganising the vault |
| filename | a rename |
| content | every edit |
| domain | re-homing a document to a different schema |

So identity is **assigned once and carried by the document**. This is why the old
`logical_id` (a hash of domain + vault-relative path) fails: moving a file severs
its history, and reorganising a vault destroys the graph.

- **`_artmind_id`** — a `uuid7`, written into markdown frontmatter by artmind on
  first ingest. Bare value, full length, no prefix. Time-ordered so `docs list`
  reads chronologically for free.
- **The registry** (`document_registry.db`) holds a **path ↔ id cache**. It is not
  authoritative — `docs reindex` rebuilds it by scanning vault frontmatter — but it
  is what lets artmind tell a *move* from a *copy*.

Nobody should ever have to type a uuid: `title` covers display, and the `docs`
commands resolve by document name.

## Resolution table

The decision every ingest makes. The discriminator for the middle rows is **whether
the registered path still exists on disk** — that is what separates a move from a
duplicate.

| `_artmind_id` in frontmatter | Registry state | Verdict |
|---|---|---|
| **present** | id known, **path matches** | **re-ingest** — bump `_version` only if the body hash changed |
| **present** | id known, registered path **no longer exists** | **move** — update the recorded path, identity survives. This is `git mv`, and it must be silent. |
| **present** | id known, registered path **still exists** and holds a different file | **refuse** — two live claimants. Require `--fork` (mint a fresh id for the newcomer) or `--adopt` (transfer identity, retire the other). |
| **present** | id **unknown** to the registry | **adopt** — trust the frontmatter and register it under that id. Do **not** mint a new one. |
| **absent** | path known | **heal** — recover the id from the registry and write it back into frontmatter |
| **absent** | path unknown | **new** — mint a `uuid7`, write it to frontmatter |

Two rows are easy to get backwards:

- **Heal is not "the id is unknown".** It is the opposite: the *frontmatter* lost
  its id (stripped by an editor, lost in a merge) while the *path* is still
  registered. The registry supplies the id back.
- **Adopt is the common case after a wipe.** A rebuilt registry, a document restored
  from an archive bundle, or a file copied in from another artmind instance all
  arrive with a valid id the registry has never seen. Minting a new id there would
  silently fork every document in the vault — and it is precisely what makes
  `docs reindex` work after Phase 8's re-ingest.

**Refusing is deliberate.** Two files sharing one id express two indistinguishable
human intents — "I copied it to make the next version" and "I used it as a
template" — so artmind must not guess. Same principle that removed the supersession
heuristics.

## Versioning

**`_content_sha256` hashes the body only, with frontmatter excluded.** Otherwise
artmind writing `_version: 2` changes the file's bytes, which changes the hash,
which triggers version 3, which rewrites frontmatter — forever. There is precedent:
`delta._compute_body_block_hashes` already parses frontmatter off before hashing.

| Change | Result |
|---|---|
| body differs from `_content_sha256` | `_version` + 1; prior chunks and observations → `_status = history` |
| only frontmatter differs | **metadata fast path** — no version, no chunking, no extraction, no observations |
| nothing differs | no-op |

`_version` is an **integer counting content states**, not re-ingests. It is
system-owned; a document's own `| Version | 2.1 |` header lifts to
**`declared_version`**, a string with no system meaning. Conflating these is why
63 of 64 documents currently carry a *string* `version` and why
`int(rec.get("version") or 1) + 1` raises on re-ingest.

`_source_commit` records the vault's git sha at ingest — provenance, not identity.
One commit can touch many documents and one document many commits, so it is a
pointer, never a version.

## The frontmatter contract

**System** — artmind writes these; extraction must never emit them. The underscore
*is* the rule: an underscore means artmind owns it.

```
_artmind_id  _version  _content_sha256  _domain  _status
_valid_from  _valid_to  _valid_time_source
_source_commit  _source_path  _source_type  _ingested_at
```

**Authored** — artmind seeds, then leaves alone:

```
title (seeded from the filename stem)  project  area  tags
declared_version  created_on  modified_on
```

`_domain` is a genuine new capability, not bookkeeping: a file finally declares
which schema extracts it, so the vault becomes self-describing and
`ingest sync` over a mixed tree becomes possible. **Frontmatter wins over
`--domain`**, which degrades to a default for files that don't declare one;
`--setDomain` re-homes a document explicitly and forces re-extraction.

## Sources that cannot carry frontmatter

| Source | Identity | Consequence |
|---|---|---|
| **binary** (pdf, pptx, docx) | its derived markdown in `<vault>/_derived/` carries `_artmind_id`; the *original* in `documents/originals/` is path-keyed | re-exporting the same deck matches on `_source_path` |
| **tabular** (csv, xlsx) | path only, recorded in the registry | **accepted limitation:** losing the registry loses table identity — `docs reindex` cannot rebuild it, because a csv has nowhere to rebuild *from* |

The move-detection rows above still apply to both: an old path gone with no rival
claimant reads as a move, not a fork.

## Derived-markdown promotion

Docling output lands in the vault, in the user's editor, beside files they do edit.
Repairing a mangled table is the first thing anyone does — and re-running conversion
would overwrite it. So an edit **promotes** the document to vault-native.

**Fingerprint.** At conversion, the body docling produced is hashed into
`_derived_sha256`. That is the signature of "untouched".

**Detect** on every ingest — hash the current body and compare:

- **equal** → nobody edited it; a fresh conversion may safely overwrite
- **different** → a human has been here → **promote**

**Promote:**

| Field | Before | After |
|---|---|---|
| `_source_type` | `pptx` | `md` |
| `_derived_sha256` | present | **removed** |
| `_source_path` | live link to the binary | historical provenance |
| location | `<vault>/_derived/<domain>/deck.md` | **`git mv` into the vault proper** |
| re-converting the binary | overwrites | **refused** — it is no longer the source |

The move is free precisely because identity is `_artmind_id` and not path: `git mv`
keeps the history, the id keeps the document.

**Automatic, and loud.** Applied without asking, then reported prominently.
Requiring a command first blocks work; overwriting silently is the thing being
prevented. Auto-promotion with a clear report is the only option that cannot lose
an edit.

**Collision** — the binary changed *and* the derived markdown was edited: **refuse
and report both sides.** Two truths diverged, and which one wins is not artmind's
call.
