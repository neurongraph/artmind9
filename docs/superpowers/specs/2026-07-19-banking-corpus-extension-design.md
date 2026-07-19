# Banking Corpus Extension Design

## Goal

Add a coherent set of new banking-corpus documents that makes temporal authority,
supersession, unresolved conflict, cross-cutting case reasoning, and regulatory
implementation materially easier to test. Existing corpus files will not change.

## New document groups

### Authority and controlled change

- A document-authority register defines the precedence of regulations, policies,
  SOPs, guides, training, templates, and reference documents, and standardises
  `draft`, `active`, `superseded`, and `withdrawn` statuses.
- An enhanced-KYC change pack contains a v2 customer-identification policy, v3
  account-opening SOP, approved training-completion record, and implementation
  action register. These documents explicitly implement FCA-COBS-2026-03 and
  supersede the corresponding prior policy/SOP where applicable.

### Temporal rates and Open Banking

- February and March 2026 SmartSaver rate schedules are independent documents.
  Each names its predecessor and successor relationship and provides an effective
  date.
- A post-deadline Open Banking outcome report records delivery status, the
  systems affected, ownership, evidence of customer-consent controls, and any
  residual exception after the June 2026 deadline.

### CASE-2026-041 evidence pack

- A case overview, incident timeline, complaint record, and retention/erasure
  decision share the identifier `CASE-2026-041` and wiki-link to one another.
- The incident assessment records 120 potentially affected customers; the later
  operations reconciliation records 118 confirmed customers. Neither document
  settles the difference. The case materials require responses to attribute and
  disclose the conflict.
- The case joins a data incident, a customer complaint, an AML/legal hold,
  customer communications, technical remediation, and governance reporting.

## Document design rules

- All new documents use the corpus metadata pattern: document ID, version,
  effective or reporting date, owner, status, audience, and related documents.
- New content is self-contained but links to exact existing documents by wiki
  name. It does not silently revise existing facts.
- Supersession is expressed explicitly in metadata and body text; historical
  versions remain valid for questions about their effective period.
- The unresolved case discrepancy is intentional and labelled as open pending
  final forensic reconciliation.

## Benchmark integration

Extend the existing functional sections in `banking_document_corpus/questions.md`
rather than creating a section called “Corpus extension.” Add approximately 12
prompts in the existing `Qnn` and `Evaluation comment` format:

- add authority selection, KYC change implementation, and monthly rate lineage
  to **B. Time, authority, and supersession**;
- add the CASE-2026-041 discrepancy and retention hold to **C. Conflicts,
  constraints, and evidence reconciliation**; and
- add Open Banking delivery status and governance follow-up to **D.
  Cross-cutting investigation, ownership, and governance**.

The benchmark is therefore organised by user-facing capability, not by the
provenance of its source documents.

## Acceptance criteria

- Only new corpus files and `questions.md` are changed; no existing corpus
  document is modified.
- All new documents can be traversed through explicit related-document links.
- The new prompts all have evaluator comments and cite the new-source evidence.
- At least one prompt requires a response to preserve the 120/118 discrepancy,
  and at least one requires use of a document's effective date or supersession.
