# Banking Temporal Metadata Design

## Goal

Make temporal authority, document state, and supersession explicit and consistently extractable across every banking knowledge-source document.

## Schema changes

Add a `temporal:` contract to all seven banking schemas. Each contract accepts the controlled date labels used by its document family and anchors time-sensitive entities to that date. Add `banking_cases_schema.yaml` for case, incident, complaint, retention-decision, impact-assessment, and remediation entities; map `cases/` to it in `schema_mapping.md`.

## Source-document convention

Every knowledge-source document receives a metadata block containing `Version`, `Status`, one canonical date field appropriate to its purpose (`Effective Date`, `Issued Date`, `Reporting Date`, `Meeting Date`, or `Opened`), `Supersedes`, and `Superseded By`. Documents without a known predecessor or successor state `None`. Historical minutes, reports, training records, and cases use their event/reporting date rather than claiming policy-style authority.

## Scope and validation

Apply the convention to policies, SOPs, products, risk/compliance, governance, guides, training, templates, reference, organization, regulations, FAQs, and cases—but not the index, questions, or mapping artefacts. Validate that every in-scope Markdown document has all five fields, every banking schema has `temporal:`, cases are mapped, and schema YAML is valid.
