# Document keys: same-named sources must not collide

Date: 2026-10-31. Status: approved design, awaiting spec review.

## Problem

Every derived artefact of a document is named by the source's bare filename stem:

- `MARKDOWNS_DIR / f"{stem}.md"`, `f"{stem}_artifacts"`, `f"{stem}_chunks"`
- the staging folder `KG_DIR / <domain> / <stem>/`

`plan_binary` (`artmind/ingest.py`) then finds "the existing document" by reading
`_artmind_id` from `MARKDOWNS_DIR / f"{stem}.md"`. Two different binaries with the
same filename in different folders therefore resolve to **one** document: the second
is ingested as a new version of the first, overwriting its markdown, chunks and
staging. Nothing fails; the job reports both files `completed`.

Seen in vault `nbs_audit_2026`: 84 distinct DocuSign certificates, all named
`Summary.pdf`, collapsed into document `01a1924a-…` at `version: 74`, whose staging
`source_path` is whichever was converted last. A second registry row (`01a1922d-…`)
also points at `markdowns/Summary.md`. `Summary_artifacts/` accumulated 216 images, so
every later `Summary.pdf` re-described the whole folder. `shadow_audit_2026` has one
more same-stem pair; no other vault on this machine has any.

Vault-native notes share the staging half of the bug: `KG_DIR / <domain> / <stem>/`
is written by stem, so two notes with the same name in different vault folders of one
domain are expected to overwrite each other's staging (to be confirmed by the first
test; their identity itself is safe, carried by `_artmind_id` in frontmatter).

## Constraints

- **Two laptops.** `markdowns/` and `kg/` are committed and shared through git; the
  registry (`document_registry.db`) is machine-local and rebuilt. A document's
  folder name must come out identical on both machines with no coordination, or two
  laptops ingesting the same new file produce two differently named folders that git
  merges silently into duplicate documents. So the name is **computed from the
  source**, never minted (a uuid in the name fails this).
- **No migration.** Existing documents keep their current bare-stem folders.
- **Move-stable.** A `git mv` of a source must not rename its folders or reconvert it.
- **Never silent.** A name clash must never again become an overwrite.

## Design

### 1. The document key

```
document_key(source) = f"{stem}~{sha256(vault_relative_posix_path).hexdigest()[:12]}"
```

e.g. `Summary~3f9a12c0b7d4`. The path includes the extension, so `X.pdf` and `X.docx`
get distinct keys. A source outside the vault is hashed by its
`_external_docs/<hash>/<name>` copy path (`external_copy_path`), already unique. Twelve
hex characters (48 bits) match `external_copy_path`; only same-stem files can collide,
so the odds are negligible, and §3's guard makes even that case visible.

`artmind/document_identity.py` owns only `document_key`. The path helpers live in
`artmind/ingest.py`, because the `ingest_env` fixture monkeypatches
`ingest.MARKDOWNS_DIR` / `ingest.KG_DIR` and a helper in another module would not see
the patch:

| Function (in `ingest.py`) | Returns |
|---|---|
| `_markdown_path(key)` | `MARKDOWNS_DIR / f"{key}.md"` |
| `_artifacts_dir(key)` | `MARKDOWNS_DIR / f"{key}_artifacts"` |
| `_chunks_dir(key)` | `MARKDOWNS_DIR / f"{key}_chunks"` |
| `_staging_dir(domain, key)` | `KG_DIR / domain / key` |
| `_derived_key(registered_path)` | a binary: its markdown's stem; a vault note: `document_key(canonical_path(note))` |
| `_note_staging_dir(domain, note, artmind_id)` | a note's staging folder: bare stem if it holds this id, else its key (collision guard) |
| `_document_staging_dir(domain, registered_path, artmind_id)` | `extract_kg`'s folder for either kind |

`markdown_path_for(...)` keeps its signature, taking the key as `stem`. Call sites to
switch: `ingest.py` (`plan_binary`, `_ingest_binary_derived`, the docling conversion,
chunk writing, `extract_kg`'s `doc_kg_dir`, the binary re-extract and resume paths),
`cli.py` (`doc_kg_dir`), `archive.py` (`doc_kg_dir`; a restore lands in a keyed
staging folder, which is new staging), `reindex.py`, `docx_convert.py`
(`artifacts_dir`), `temporal._read_doc_body` (reads a `:Document`'s recorded `path`,
bare-stem lookup as the fallback) and `retime.staged_markdown_path` (same). A test fails if a `MARKDOWNS_DIR / f"{…}.md"`, `_artifacts`,
`_chunks` or `KG_DIR / … / ….stem` expression appears outside the `ingest.py` helpers.

A key is a folder name only. The `:Document` node's `title` and `name` still come from
the source filename (`Summary`, `Summary.pdf`); its `path` is the key's markdown.

### 2. Choosing a source's key

A key is chosen once, at first ingest, and found again afterwards:

1. **Known source.** A staging `document.json` in the domain whose `source_path`
   equals this source's vault-relative path → that folder's name. This finds every
   existing document under its old bare-stem folder (the no-migration rule) and every
   new one under its `~hash` folder. Lookup: the direct bare-stem folder first, then a
   scan of the domain's staging `document.json` files (one read per folder, cached for
   the run).
2. **Moved binary.** No path match, but a document in the domain has
   `source_sha256` equal to this source's and its recorded `source_path` no longer
   exists → treat as a move: keep the key, update `source_path` (the
   `_repoint_staged_document` path vault notes already use).
3. **New.** Otherwise `document_key(source)`.

Vault notes keep their id-based resolution (`_locate_staged`); only the name of a
note's **first** staging folder changes, from `<stem>` to `document_key(note)`.

**One rule names a note's staging AND chunks** (`ingest._note_key`): a note is
*legacy* when its bare-stem staging folder holds its `_artmind_id`; then both its
staging folder and its `<stem>_chunks/` use the bare stem, so a note ingested before
keys keeps its chunk cache (`extract-kg`, resume-extract) and a changed one does not
orphan `<stem>_chunks`. Otherwise both use its key (the full-digest key under the
collision guard). An empty id never matches a bare folder, so it yields the key. A
converted binary's chunks are named by its markdown's stem.

**An early claim.** docling writes `<key>.md` long before `conversion.json`, so a
conversion interrupted in between left an ownerless keyed markdown. `_ingest_binary_derived`
therefore writes the `{source_sha256, source_type, source_path}` record to
`<key>_chunks/conversion.json` *before* converting (overwritten by the conversion
record), and a keyed name counts as taken only when a *recorded owner* names another
source: an ownerless file at a source's own path-hash name is that source's, so a retry
reconverts under the same key. The claim does not make a failed conversion look
`unchanged`: that compares the staged baseline's `source_sha256`, which a failed run
never wrote.

**Recording a move.** When a binary is found by its bytes under a name whose recorded
`source_path` differs from its own, `_ingest_binary_derived` rewrites `source_path` in
the sidecar and in the staged `document.json` (only that field: `path`/`name` are the
converted markdown's), so the old path no longer holds the claim and a different file
added there gets its own key (the full-digest key, the original being taken).

`plan_binary` reads the existing document from the key's markdown, never from a bare
`f"{stem}.md"`.

**docling naming.** The docling CLI names its output after the input file. Conversion
runs on a symlink named `<key><ext>` in a scratch directory under `LOGS_DIR`, so docling
writes `<key>.md`, `<key>.json` and `<key>_artifacts/` directly; image links need no
rewriting. In-process docx conversion is given `artifacts_dir(key)`.

### 3. Collision guard

Before writing into `staging_dir(domain, key)` or `markdown_path(key)`, read the
occupant's recorded `source_path` (staging `document.json`, else the markdown's
provenance). If it holds a **different** existing source, the newcomer's key becomes
`f"{stem}~{full 64-hex digest}"` and a warning names both sources. An occupant is never
overwritten by another source.

### 4. Repairing nbs_audit_2026 (one-off, after the fix ships)

1. `artmind docs retire --domain contracts_vector --documentName <id>` for
   `01a1924a-…` and `01a1922d-…` (not `docs archive`: that deletes the source PDF).
2. Remove their artefacts so no `Summary.pdf` matches rule 1 and resurrects a retired
   id: `kg/contracts_vector/Summary/`, `markdowns/Summary.md`, `Summary_chunks/`,
   `Summary_artifacts/`. The vault repo commits the deletion (Obsidian Git).
3. `ingest sync` the 84 `Summary.pdf` files: each becomes its own `Summary~<hash>`
   document.
4. Resume the stopped job's remaining files (53 queued, 1 interrupted, job
   `0329980f-…`), with the fixed code installed and the daemons restarted.

The retirement records stay in `curation/lifecycle/` as harmless history. A general
split command is not built unless another vault needs one.

## Testing

Hermetic, in `test/`, written first:

- Two same-named PDFs in different folders → two documents, distinct markdown,
  artifacts, chunks and staging folders; neither has a version above 1.
- Two same-named notes in one domain → distinct staging folders (confirms the note
  half of the bug before fixing it).
- Re-ingest of an existing bare-stem binary keeps its folder and version.
- A moved binary (same bytes, new path, old path gone) keeps its key.
- `document_key` depends only on the vault-relative path (two "laptops", no registry).
- `X.pdf` and `X.docx` in one folder get distinct keys.
- Collision guard: a key whose folder holds another source → full-digest key + warning,
  occupant untouched.
- No hand-built derived path outside the `ingest.py` helpers (source scan).

Then end to end in a scratch vault against a real Neo4j with `ARTMIND_NO_PROXY=1`
(two same-named PDFs, ingest, check registry, staging and graph) before step 4.

## Out of scope

- Renaming existing documents' folders to keys.
- Tables (`xlsx`/`csv` → structured store), which have their own naming.
- The vault-less ad-hoc flow (`_ingest_binary_or_adhoc`, `ORIGINALS_DIR`).
- Name-based supersession matching in `temporal.py` (`by_stem_name`).
