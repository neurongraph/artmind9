# 07 — Benchmark round 3 (banking_corpus, 36 questions)

Per-question grades for the round-3 benchmark run, 2026-10-21. Grades come from the
benchmark's evaluation comments (`benchmarking/questions.md`) applied to each answer, the
vault's own documents and the recorded trace (six parallel graders, one rubric). Question
ids refer to `benchmarking/questions.md`. Document paths are the public sample corpus's
file names. No answer text is reproduced here.

**Grade:** *correct* = the substance of the evaluation comment is met, the right version
of every document was used and disagreements were disclosed; *partial* = useful but misses
an expected document, a version, or adds unsupported detail; *wrong* = a material part is
incorrect or the answer concludes data is absent.
**Cause** = the primary reason an answer is not correct (secondary after the slash):
*routing* (wrong or missing step: domains, time, store, conflicts), *retrieval* (right
route, the evidence was not pulled or not followed), *synthesis* (evidence was in hand,
the answer is wrong, embellished or incomplete), *data* (graph or ingest state hides the
evidence).

## Table

Columns: turns, wall seconds, cost in USD, `views list` calls (V), other artmind command
families used (`×n` = repeats; `domains-overview` and `views list` omitted).

| Id | Sec | Grade | Turns | s | USD | V | Commands | Cause | Missing expected documents |
|---|---|---|---|---|---|---|---|---|---|
| Q01 | A | correct | 14 | 148 | 0.15 | 2 | pattern10 entity-context vector-text×2 chunks×3 | — | guides/complaint_resolution_guide.md |
| Q02 | A | correct | 7 | 150 | 0.05 | 1 | vector-text chunks | — | — |
| Q03 | A | partial | 10 | 54 | 0.08 | 2 | entity-resolve×2 entity-context×2 | retrieval / synthesis | — |
| Q04 | A | partial | 8 | 60 | 0.07 | 1 | entity-resolve entity-context | routing / retrieval | reference/interest_rate_schedule_2026.md |
| Q05 | A | partial | 8 | 47 | 0.07 | 1 | pattern10×2 vector-text | routing / retrieval | policies/policy_complaints_v3.md, reference/interest_rate_schedule_2026.md |
| Q06 | A | partial | 8 | 48 | 0.07 | 2 | vector-text pattern10 | routing / retrieval | policies/policy_complaints_v3.md, guides/complaint_resolution_guide.md |
| Q07 | B | wrong | 8 | 165 | 0.06 | 2 | vector-text chunks | routing / data | policies/policy_complaints.md |
| Q08 | B | correct | 13 | 148 | 0.09 | 2 | vector-text×3 pattern10 chunks document-history | — | — |
| Q09 | B | partial | 10 | 70 | 0.07 | 1 | vector-text×2 pattern10 chunks | routing / data | policies/policy_complaints.md |
| Q10 | B | wrong | 22 | 953 | 0.65 | 1 | pattern10×5 entity-resolve×2 entity-history entity-context vector-text×3 text2cypher×3 document-history | routing / data | reference/interest_rate_schedule_2026.md |
| Q11 | B | partial | 8 | 40 | 0.10 | 1 | vector-text×2 chunks | data / routing | reference/interest_rate_schedule_2026.md |
| Q12 | B | partial | 9 | 42 | 0.06 | 2 | entity-resolve pattern10×2 | routing / synthesis | regulations/regulatory_circulars_2026.md |
| Q13 | C | wrong | 8 | 98 | 0.06 | 1 | vector-text×2 chunks | routing / data | policies/policy_complaints.md |
| Q14 | C | partial | 10 | 61 | 0.09 | 1 | entity-resolve pattern10×4 | synthesis / routing | — |
| Q15 | C | partial | 11 | 164 | 0.08 | 1 | pattern10×2 vector-text×3 | synthesis / retrieval | policies/policy_privacy.md |
| Q16 | C | partial | 10 | 310 | 0.07 | 2 | entity-resolve entity-context×3 | routing / synthesis | sop_procedures/sop_exception_handling.md, guides/escalation_matrix.md |
| Q17 | D | partial | 10 | 233 | 0.06 | 2 | entity-resolve vector-text chunks×2 | routing / synthesis | organization/systems.md, policies/policy_privacy.md |
| Q18 | D | partial | 13 | 73 | 0.13 | 1 | entity-resolve pattern10×3 vector-text×3 chunks | routing / synthesis | reference/technology_production_runbook.md, policies/policy_complaints_v3.md, risk_compliance/risk_appetite_statement_2026.md |
| Q19 | D | partial | 9 | 152 | 0.07 | 1 | vector-text pattern10 chunks | routing / retrieval | reference/technology_production_runbook.md, reference/technology_application_landscape.md, organization/departments.md |
| Q20 | D | partial | 7 | 35 | 0.05 | 1 | entity-resolve entity-context | routing / synthesis | sop_procedures/sop_standing_orders.md, sop_procedures/sop_account_closure.md, organization/departments.md, governance/board_risk_committee_charter.md |
| Q21 | D | partial | 8 | 108 | 0.06 | 1 | pattern10×3 | retrieval / synthesis | sop_procedures/sop_account_opening.md, policies/policy_customer_identification_v2.md, policies/policy_aml.md, regulations/regulatory_circulars_2026.md |
| Q22 | D | partial | 7 | 37 | 0.06 | 2 | vector-text pattern10 | retrieval / routing | risk_compliance/risk_appetite_statement_2026.md, risk_compliance/audit_report_q4_2025.md, governance/board_risk_committee_charter.md |
| Q23 | D | partial | 10 | 73 | 0.11 | 1 | pattern10×3 vector-text×2 | retrieval / synthesis | templates/email_templates.md, templates/sms_templates.md, organization/departments.md |
| Q24 | D | partial | 13 | 320 | 0.11 | 1 | vector-text×4 pattern10 pattern×2 | retrieval / routing | reference/technology_application_landscape.md, organization/organisation_model.md, organization/departments.md, policies/policy_complaints_v3.md, governance/board_risk_committee_charter.md |
| Q25 | B | partial | 6 | 46 | 0.06 | 1 | pattern10 | data / routing | policies/policy_customer_identification_v2.md, governance/document_authority_register.md |
| Q26 | B | correct | 12 | 107 | 0.09 | 2 | vector-text×3 chunks×2 pattern10 | — | policies/policy_customer_identification_v2.md, sop_procedures/sop_account_opening_v3.md |
| Q27 | B | correct | 8 | 198 | 0.06 | 1 | vector-text chunks | — | — |
| Q28 | C | correct | 16 | 132 | 0.14 | 1 | entity-resolve entity-context×2 vector-text×2 pattern×3 pattern10 chunks text2cypher | — | cases/case_2026_041_overview.md |
| Q29 | C | correct | 10 | 172 | 0.10 | 1 | pattern10×3 vector-text | — | policies/policy_privacy.md, policies/policy_aml.md |
| Q30 | D | correct | 7 | 72 | 0.05 | 2 | pattern10 | — | regulations/regulatory_circulars_2026.md |
| Q31 | D | partial | 13 | 104 | 0.11 | 1 | pattern10×4 vector-text×3 | synthesis / retrieval | — |
| Q32 | E | partial | 22 | 563 | 0.17 | 1 | db bridge text2sql×6 db sql×3 vector-text×3 chunks pattern10×2 | synthesis / routing | policies/policy_complaints_v3.md |
| Q33 | E | partial | 10 | 161 | 0.08 | 1 | db bridge db sql×3 pattern10 | routing / data | policies/policy_complaints_v3.md |
| Q34 | E | correct | 14 | 197 | 0.11 | 2 | db bridge db sql×4 db schema vector-text chunks | — | structured/customers.csv |
| Q35 | E | partial | 12 | 64 | 0.08 | 2 | db sql×3 pattern vector-text chunks | synthesis | — |
| Q36 | E | partial | 21 | 202 | 0.16 | 2 | db bridge db schema text2sql db sql×2 vector-text×7 pattern10 | synthesis | — |
