# Vault content model: markdown-first, binaries as referenced attachments

The Vault (ADR 0002, the authoritative source of knowledge) is markdown-first: the
editable surface is **markdown only**. Images and binary artifacts (pdf, pptx) live in
the Vault as **referenced attachments** (e.g. an `attachments/` area resolved relative
to the Vault root), ingested via artmind's existing docling/image-description path but
never edited inline. A `document` Card edits markdown; an attachment gets at most a
read-only viewer Card.

Why: keeping the editable, round-trippable surface as markdown keeps the whole
edit → re-ingest loop coherent, while preserving original artifact fidelity (unlike
convert-on-import) and still letting artmind derive knowledge from richer sources.
Rejected: markdown-only convert-on-import (loses originals) and fully-mixed
first-class editing of every type (far too much surface for v1).
