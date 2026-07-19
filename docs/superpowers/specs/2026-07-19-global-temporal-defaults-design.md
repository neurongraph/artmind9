# Global Schema-Opt-In Temporal Defaults Design

## Goal

Provide deterministic temporal fallbacks globally while applying them only to domains whose schema explicitly declares `temporal:`.

## Configuration

Schemas that opt in may declare:

```yaml
defaults:
  valid_from: ingestion_date
  valid_to: null
  superseded_by: null
  time_source: default_ingestion
  valid_from_inferred: true
```

Schemas without `temporal:` remain untouched.

## Rules

Stated header and frontmatter dates always win. If no mapped source date exists, `valid_from: ingestion_date` writes the UTC ingestion date and configured provenance. `valid_to` and `superseded_by` remain null until explicit supersession is detected or manually asserted; defaults never create a `SUPERSEDES` edge. Null represents open-ended validity, so no artificial future date is required.

## Testing

Cover explicit header dates, frontmatter dates, ingestion-date defaults, null open-ended validity, explicit-only supersession, and no temporal writes for schemas without `temporal:`.
