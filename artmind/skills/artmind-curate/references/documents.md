# Documents: supersession, retirement, archive

Three different acts, from lightest to heaviest. Pick by what is true about the document:

| What is true | Act | Undo |
|---|---|---|
| A newer document replaces it (a new version, a revised policy) | `ingest supersede` | `ingest unsupersede` |
| Its content should leave every index, with no replacement | `docs retire` | `docs restore` |
| It must leave the vault and the graph entirely | `docs archive` | `docs restore-from-archive` |

Each act is a record file under `.artmind/data/curation/` (`supersessions/`, `lifecycle/`) and
travels with the vault (travel.md).

## Supersession

A supersession records `SUPERSEDES` from the newer to the older document, sets the older one's
`valid_to`, and retires it. Its facts move to history but stay reachable: dated questions ("what was
the rate in March?") are answered from that history. **Prefer it to a bare retire whenever a newer
document exists.**

Find the supersessions the documents declare:

```bash
artmind ingest detect-supersession --domain <d> --dryRun --compact
artmind ingest detect-supersession --domain <d> --compact
```

It reads "## Supersession Notice" sections and `| Supersedes |` metadata rows, and, when the domain
schema sets `temporal.defaults.supersede_on_title_family`, version chains among documents of one
title family ordered by date. Direction comes from explicit links first, then `Status`, then the
documents' dates, never version strings; an ambiguous pair is left alone.

Assert one by hand:

```bash
artmind ingest supersede --domain <d> --newer "<newer doc>" --older "<older doc>" \
  [--scope document|section|clause] [--effective YYYY-MM-DD] --dryRun --compact
```

Check `effect`: both names must have resolved to the documents the user meant. `--scope section` or
`clause` records a partial replacement; `--effective` sets the date the change takes effect.

Fix one written the wrong way round: `artmind ingest unsupersede --domain <d> --newer "<as asserted>"
--older "<as asserted>" --dryRun`, then the real run, then `ingest supersede` the right way.
`unsupersede` restores the older document when no other supersession points at it.

Check the result with `artmind query document-history --domain <d> --documentName "<name>"
--compact`.

## Retire and restore

`docs retire` moves a document and everything it asserted from latest to history, with no
replacement. Its observations stay in storage, reachable by asking for them, and leave every index;
entities nothing else asserts disappear on the rebuild.

```bash
artmind docs retire --domain <d> --documentName "<name>" --dryRun --compact
artmind docs restore --domain <d> --documentName "<name>" --dryRun --compact   # exact inverse, same entity ids
```

A retired document stays retired across re-ingest and on other machines.

## Archive

`docs archive` is the only removal artmind has: it bundles the document (staged KG, vault markdown,
original binary, manifest) under `ARTMIND_ARCHIVE_DIR`, removes it from the graph and **deletes its
file from the vault**. Confirm with the user explicitly before running it, and show the `--dryRun`
effect first.

```bash
artmind docs archive --domain <d> --documentName "<name>" --dryRun --compact
artmind docs archived [--domain <d>] --compact                  # what has been archived
artmind docs restore-from-archive --id <_artmind_id> [--toPath <p>] [--newId <id>]   # lands as history; then docs restore to promote
```

`restore-from-archive` has no working `--dryRun` yet. Deleting an archive bundle is a filesystem act
outside artmind and cannot be undone.
