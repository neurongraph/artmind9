# Banking Document Corpus — Schema Mapping

Maps each schema to its source folders and document files for artmind ingestion.

Domains are now hierarchical: every schema below is a child of the abstract
`banking` parent domain (`banking_schema.yaml`, never ingested directly — it
only defines the shared FIBO-informed common ontology and temporal defaults
that all children inherit via `artmind domains harmonize`). Querying
`--domain banking` rolls up results across every child below.

| Schema | Folders | Documents |
|---|---|---|
| `banking.organization` | organization/ | business_ontology.md, organisation_model.md, departments.md, systems.md, branches.md, products.md, steps.md |
| `banking.products` | products/, faqs/ | smartsaver_terms_conditions.md, product_pricing_guide_2026.md, product_faq.md, website_faq.md |
| `banking.sop_guides` | sop_procedures/, guides/ | sop_account_opening.md, sop_account_closure.md, sop_kyc_verification.md, sop_aml_screening.md, sop_standing_orders.md, sop_direct_debits.md, sop_change_of_address.md, sop_exception_handling.md, escalation_matrix.md, fraud_investigation_procedure.md, complaint_resolution_guide.md |
| `banking.policy` | policies/ | policy_aml.md, policy_customer_identification.md, policy_fraud.md, policy_information_security.md, policy_privacy.md, policy_complaints.md, policy_operational_risk.md, policy_retention.md |
| `banking.risk_governance` | risk_compliance/, governance/, regulations/ | audit_report_q4_2025.md, risk_appetite_statement_2026.md, risk_assessment_template.md, compliance_bulletins_2026.md, credit_risk_assessment_guide.md, governance_framework_overview.md, internal_audit_charter.md, board_risk_committee_charter.md, board_risk_committee_minutes_q1_2026.md, regulatory_circulars_2026.md |
| `banking.reference` | reference/ | interest_rate_schedule_2026.md, incident_response_plan.md, technology_production_runbook.md, technology_application_landscape.md, welcome_pack.md |
| `banking.communications` | templates/, training/ | email_templates.md, sms_templates.md, call_centre_script.md, compliance_training_manual.md, product_training_manual.md, branch_operations_training.md |
| `banking.cases` | cases/ | case_2026_041_overview.md, case_2026_041_incident_timeline.md, case_2026_041_complaint_record.md, case_2026_041_retention_decision.md |

## Structured store (`structured/`)

Not a document domain — these csv files feed the **structured data ingestion**
pipeline (`artmind ingest sync <file> --domain banking`), landing in DuckDB/
parquet + the registry rather than the graph. Ingested against the literal
`banking` domain (not a `banking.*` child) so `artmind db list/schema --domain
banking` finds them directly, and so the mapping proposer's `entity_listing(["banking"])`
call rolls up and sees `PRODUCT` entities (from `banking.products`) and
`ROLE_PERSON` entities (branch managers, from `banking.organization`) across
every sibling domain — `product`/`branch`/`assigned_agent`/`reviewed_by`
columns below are deliberately spelled to match those entity names exactly.

| File | Rows | Purpose |
|---|---|---|
| `customers.csv` | 24 | Customer master: segment (Standard/Premier/Private), primary product, branch, join date |
| `vulnerable_customers.csv` | 7 | FCA four-drivers vulnerability flags (Health/Life Events/Resilience/Capability) for a subset of customers, reviewed by the customer's branch manager |
| `agents.csv` | 16 | Staff roster: the 12 branch managers from `organization/branches.md` plus 4 central complaints handlers |
| `complaints.csv` | 20 | Complaint history: category/severity/status per `policies/policy_complaints.md`, product, assigned agent, resolution time, compensation |
| `csat_scores.csv` | 30 | CSAT survey scores by customer and agent, across Branch/Phone/Online/Mobile App channels |

All customer/agent/branch/product names are fictional and consistent with the
rest of this corpus (FirstUK Bank).
