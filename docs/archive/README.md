# Design History Archive

This directory holds **design specs, plans, and process records** from shipped features — documentation that was authoritative *while* the work was happening, but is now historical.

Nothing here should be treated as current documentation. For current architecture, design, and capabilities, see:
- [../CONTEXT.md](../CONTEXT.md) — current domain glossary
- [../INSTALL.md](../INSTALL.md) — installation and runtime reference
- [../document-identity.md](../document-identity.md) — current identity/versioning mechanism
- [../projection-pipeline.md](../projection-pipeline.md) — current observation→projection pipeline
- [../stores-and-repos.md](../stores-and-repos.md) — current store/repo topology

## Directory structure

- **`superpowers/plans/`** — 20 implementation plans (one per shipped feature: artmind-update, cli-hyphen-convention, poole-hierarchical-domains, pull-kg, session-graph-snapshot, artmind-wizard, cross-domain-conflicts-and-temporality, chat-ui-redesign, banking-corpus-qa-benchmark, staging-commit-model, admin-dashboard-layout, banking-corpus-extension, banking-temporal-metadata, global-temporal-defaults, incremental-supersession-hardening, supersession-integrity-fixes, structured-data-ingestion-plan, entity-supersession-history-zone, structured-semantic-classification-pipeline-plan, feature summary).

- **`superpowers/specs/`** — 15 design specs (one per plan above). These are the "design-before-building" artifact; paired with each plan.

- **`redesign/`** — The observation/projection redesign process record (Aug 2026). Includes the phase plan, change inventory, skills review, 8 phase implementation notes, 2 runbooks (Phase 3 and Phase 8), quality scorecard (the before/after regression baseline), and phase migration review. This is *how Phase 8 was built*, not how the system works now. See `../projection-pipeline.md` etc. for the current design.

- **`cross_domain_conflicts/`** — Early design drafts for cross-domain query and temporality (Jul 2026). Consolidated into the `superpowers/specs/2026-07-04-cross-domain-conflicts-and-temporality-design.md` pair; kept here for reference.

- **Root of archive** (`plan.md`, `admin-ui-plan.md`, `admin-ui-spec.md`, `INCREMENTAL_INGESTION.md`) — Historical individual specs/plans that predate the superpowers structure or superseded by v2 (e.g., `INCREMENTAL_INGESTION.md` is explicitly superseded by `../INCREMENTAL_INGESTION_v2.md`).

---

## Why archive at all?

These documents form the permanent record of *how each decision was made*. Keeping them browsable (not deleted) serves:

1. **Audit trail** — future maintainers can read why a design choice was made, what trade-offs were considered, and what edge cases were discovered.
2. **Regression history** — if a defect is reported, cross-referencing the implementation notes often clarifies what was intentional vs. what was a known limitation at ship time.
3. **Pattern learning** — reading the full plan → spec → implementation cycle reveals the project's decision-making patterns and can inform future designs.

---

## How to use the archive

- **Looking for implementation rationale?** Start with the plan paired with the spec, then scan the corresponding redesign phase notes (if applicable).
- **Debugging a feature?** The implementation notes often record edge cases, workarounds, or deferred work that didn't make it into the spec.
- **Proposing a change to shipped code?** The plan + spec often justify why it was built that way; reading both before proposing a refactor can save cycles.

**But:** If you find yourself frequently reading archive docs to understand how to *use* the system or how to *build new features*, that's a signal the live documentation (above) is incomplete. File an issue to surface the missing doc.
