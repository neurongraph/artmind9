# Banking Corpus Q&A Benchmark

Use these prompts verbatim to compare Artmind with a vector-only RAG system. The
questions intentionally range from ordinary service retrieval to questions that
need document relationships, authority, dates, and multi-step reasoning.

Each **Evaluation comment** is for the evaluator—not the system under test. It
identifies the evidence and behaviour expected of a good answer. A strong answer
should cite its sources, use the guidance applicable at the requested time,
distinguish superseded from active material, and disclose an unresolved
disagreement rather than silently inventing certainty.

## A. Baseline service and operations

### Q01 — Supporting a vulnerable customer changing address

**Question:** A vulnerable customer is moving home and finds forms difficult. How should I help them change their address safely, including identity checks, affected accounts, and any escalation?

**Evaluation comment:** Tests a realistic multi-document operational response. It should connect the customer-treatment guidance in `guides/complaint_resolution_guide.md` and/or `training/branch_operations_training.md` with the verification, multi-account, joint-account, mortgage, and escalation steps in `sop_procedures/sop_change_of_address.md`.

### Q02 — Repeated standing-order failures

**Question:** A customer says their monthly standing order keeps failing. What should I check, what happens after a failed attempt, and what are the customer’s options?

**Evaluation comment:** Tests process retrieval with a useful causal chain. The answer should use `sop_procedures/sop_standing_orders.md` to cover common failure causes, notification, one retry, staff review/suspension, correction or cancellation, rather than confusing a standing order with a direct debit.

### Q03 — Stop a direct debit versus a standing order

**Question:** What is the difference between cancelling a direct debit and cancelling a standing order, and when does each cancellation take effect?

**Evaluation comment:** Tests entity distinction across closely related payment processes. Reconcile `sop_procedures/sop_direct_debits.md` and `sop_procedures/sop_standing_orders.md`: both describe immediate cancellation, but the mandate/biller relationship and the customer-controlled payment instruction are different.

### Q04 — SmartSaver features and rate structure

**Question:** What are the key features of the SmartSaver account, and how does its interest rate vary by balance?

**Evaluation comment:** Baseline product question. Combine the product terms and features in `products/smartsaver_terms_conditions.md` with the tiered AER, daily accrual, monthly payment, and variable-rate basis in `reference/interest_rate_schedule_2026.md`.

### Q05 — Incorrect-interest complaint

**Question:** A customer says their savings interest was calculated incorrectly. How should we investigate and resolve the complaint?

**Evaluation comment:** Tests product-to-complaints traversal. It should use the active `policies/policy_complaints_v3.md` for investigation and remedy, plus `reference/interest_rate_schedule_2026.md` and `products/smartsaver_terms_conditions.md` for the rate-calculation evidence. The answer should not assume the customer is correct without reconciliation.

### Q06 — Direct-debit claim and complaint escalation

**Question:** A customer was charged by direct debit after they cancelled it and does not accept the initial decision. What should happen next?

**Evaluation comment:** Tests hand-off between processes. The answer should follow the Direct Debit Guarantee claim and cancellation evidence in `sop_procedures/sop_direct_debits.md`, then the formal complaint route in `policies/policy_complaints_v3.md` and `guides/complaint_resolution_guide.md`.

## B. Time, authority, and supersession

### Q07 — Complaint authority before the revision

**Question:** On 15 May 2026, who could approve £300 compensation for a complaint, and why?

**Evaluation comment:** Tests historical authority. The answer must use `policies/policy_complaints.md` (v2.0, effective 2026-01-15) because the question predates v3.0. It should identify the relevant escalation and compensation tables and note their inconsistency instead of presenting an unqualified single approver.

### Q08 — Complaint authority after the revision

**Question:** On 15 June 2026, who can approve £300 compensation for a complaint? Is any earlier guidance still applicable?

**Evaluation comment:** Tests temporal filtering and formal supersession. `policies/policy_complaints_v3.md` is effective from 2026-06-01 and explicitly supersedes v2.0 in full to resolve the conflicting approval thresholds. A correct answer should apply v3.0 and explicitly exclude the old thresholds.

### Q09 — Explain the policy history, not just the current rule

**Question:** How did complaint-compensation approval rules change in 2026, and what issue prompted the change?

**Evaluation comment:** Tests document lineage. The answer should compare the same Document ID, COM-POL-006, across `policies/policy_complaints.md` and `policies/policy_complaints_v3.md`, explain the v2 internal inconsistency, and describe v3’s stated resolution and effective date.

### Q10 — Rate applicable in the past

**Question:** What SmartSaver Tier 1 rate applied on 15 October 2025, and how did it compare with the rate on 15 January 2026?

**Evaluation comment:** Tests a date-specific historical lookup rather than a “current rate” answer. The recent-rate history in `reference/interest_rate_schedule_2026.md` contains both dates and should be used to compare them accurately.

### Q11 — Current schedule versus future effective change

**Question:** A rate review happens around the 15th of a month. When would the revised rate normally take effect, and how would customers be notified?

**Evaluation comment:** Tests temporal process interpretation from `reference/interest_rate_schedule_2026.md`: distinguish the review date from the effective date, and retrieve the notification channels. It should avoid treating the document’s January current rate as a permanent promise.

### Q12 — Regulatory status changed over time

**Question:** What was FirstUK’s status for enhanced KYC for high-risk customers before 1 March 2026, and what did the Board Risk Committee report later in Q1?

**Evaluation comment:** Tests status evolution across linked governance material. Compare `regulations/regulatory_circulars_2026.md`, which records the requirement, deadline, and “under review” status, with `governance/board_risk_committee_minutes_q1_2026.md`, which reports procedures updated and staff trained. Attribute each status to its document/time rather than claiming one timeless status.

### Q25 — Which KYC rule controls after March 2026?

**Question:** An entity customer applied on 10 March 2026, has a beneficial owner with 30% control, and plans to deposit £15,000. Which policy and procedure control, and what must happen before activation?

**Evaluation comment:** Tests authority and effective-date selection. Use `governance/document_authority_register.md`, `policies/policy_customer_identification_v2.md`, and `sop_procedures/sop_account_opening_v3.md`; both controlled documents apply from 2026-03-01 and supersede earlier versions.

### Q26 — Prove the KYC change was implemented

**Question:** What evidence shows that FirstUK implemented enhanced KYC by the 1 March 2026 deadline, and what remains to be monitored?

**Evaluation comment:** Tests regulatory traceability across `regulations/regulatory_circulars_2026.md`, `risk_compliance/enhanced_kyc_implementation_register.md`, `training/enhanced_kyc_training_completion_2026.md`, and the new policy/SOP. Separate completed implementation from the first quarterly monitoring cycle.

### Q27 — Select the correct monthly rate schedule

**Question:** Which SmartSaver Tier 1 rate applied on 20 February 2026 and on 20 March 2026? Why should the January schedule not be used?

**Evaluation comment:** Tests explicit supersession. `reference/interest_rate_schedule_2026_02.md` controls February and is superseded by `reference/interest_rate_schedule_2026_03.md` from 2026-03-01.

## C. Conflicts, constraints, and evidence reconciliation

### Q13 — Resolve a historical internal inconsistency responsibly

**Question:** I found two different approval boundaries for the same complaint compensation decision in the old policy. Which one should I follow?

**Evaluation comment:** Tests conflict detection. The response should identify the inconsistency in `policies/policy_complaints.md` (v2) and its correction in `policies/policy_complaints_v3.md`, then ask for the decision date or apply the current v3 policy where the decision is current. It must not conceal the conflict.

### Q14 — Erasure request with an AML hold

**Question:** A customer asks us to delete all their data, but their account is subject to an AML-related regulatory hold. What can we delete, what must we retain, and how should we respond?

**Evaluation comment:** Tests policy reconciliation. Use `policies/policy_privacy.md` for erasure exceptions and `policies/policy_retention.md` for AML/POCA and regulatory-hold retention. The answer should distinguish the customer right from the legal exception, not issue an unconditional deletion promise.

### Q15 — Account closure while a fraud investigation is open

**Question:** Can we close a customer’s account when there is an outstanding fraud investigation and a complaint about the same transactions?

**Evaluation comment:** Tests constraints across processes. `sop_procedures/sop_account_closure.md` requires no outstanding fraud investigation or active dispute; `policies/policy_privacy.md`, `policies/policy_retention.md`, `policies/policy_fraud.md`, and `policies/policy_complaints_v3.md` add related treatment and record-retention considerations. The result should explain the blocking conditions and escalation path.

### Q16 — An exception outside risk appetite

**Question:** A business unit wants an exception to a risk limit because of a commercial opportunity. Who needs to be involved and what must be documented before it can proceed?

**Evaluation comment:** Tests exception governance. Trace `risk_compliance/risk_appetite_statement_2026.md` through its escalation-and-exception process, then connect it to `sop_procedures/sop_exception_handling.md`, `guides/escalation_matrix.md`, and the Board Risk Committee’s remit in `governance/board_risk_committee_charter.md` as appropriate to the threshold and materiality.

### Q28 — Report the case impact without hiding the difference

**Question:** How many customers were affected by CASE-2026-041? Give the answer suitable for a Board risk report.

**Evaluation comment:** Tests an intentional unresolved conflict. `cases/case_2026_041_overview.md` and `cases/case_2026_041_incident_timeline.md` distinguish 120 potentially affected customers from 118 confirmed customers. Attribute both; do not collapse them into a final total.

### Q29 — Erasure request during the open case

**Question:** A CASE-2026-041 customer wants all data erased now. What can we action, what must remain, and when is the decision reviewed?

**Evaluation comment:** Tests case-specific privacy and retention reasoning using `cases/case_2026_041_retention_decision.md`, `policies/policy_privacy.md`, `policies/policy_retention.md`, and `policies/policy_aml.md`.

## D. Cross-cutting investigation, ownership, and governance

### Q17 — Open Banking access, consent, and retention

**Question:** What must the bank provide for Open Banking, which customer data can be shared, how is consent handled, and how long can the data be held?

**Evaluation comment:** Tests a regulatory-to-system-to-privacy chain. Use the API scope, OAuth, consent, and 90-day condition in `regulations/regulatory_circulars_2026.md`; link to relevant systems in `organization/systems.md` and data protection principles in `policies/policy_privacy.md`.

### Q18 — A payment outage causing customer harm

**Question:** Payment processing is unavailable and customers are missing scheduled payments. Give the immediate operational response, severity/escalation considerations, customer communication, and the risk/governance follow-up.

**Evaluation comment:** Tests broad graph traversal. It should connect `reference/technology_production_runbook.md`, the severity and incident process in `reference/incident_response_plan.md`, `guides/escalation_matrix.md`, `policies/policy_complaints_v3.md`, `risk_compliance/risk_appetite_statement_2026.md`, and `governance/board_risk_committee_charter.md`. A strong answer distinguishes live incident actions from later remediation and reporting.

### Q19 — Fraud alert false positives

**Question:** Fraud controls are blocking many legitimate transactions and generating customer complaints. Which teams and systems should investigate, what evidence is needed, and how should affected customers be treated?

**Evaluation comment:** Tests incident, system, fraud, and customer-service connections. The technology runbook explicitly describes a high fraud-alert-rate issue; combine it with `policies/policy_fraud.md`, `guides/fraud_investigation_procedure.md`, `reference/technology_application_landscape.md`, `policies/policy_complaints_v3.md`, and `organization/departments.md`.

### Q20 — Audit finding to remediation status

**Question:** The Q4 2025 audit found weaknesses in standing-order audit trails and account-closure documentation. What were the required actions, who should own them, and what evidence would demonstrate closure?

**Evaluation comment:** Tests finding-to-control-to-owner reasoning. Start with the required actions and deadlines in `risk_compliance/audit_report_q4_2025.md`; connect the relevant SOPs, Operations ownership in `organization/departments.md`, and the Board Risk Committee’s responsibility to monitor audit findings and remediation.

### Q21 — KYC exception through onboarding, compliance, and audit

**Question:** An online account was opened without identity verification. What is the immediate remediation, who approves any exception, and which control bodies need visibility?

**Evaluation comment:** Tests an audit-like case across several layers. `sop_procedures/sop_exception_handling.md` gives the concrete scenario; connect it with `sop_procedures/sop_account_opening.md`, `sop_procedures/sop_kyc_verification.md`, `policies/policy_customer_identification.md`, `policies/policy_aml.md`, `regulations/regulatory_circulars_2026.md`, and `governance/internal_audit_charter.md`. The answer must not normalize a policy exception as routine approval.

### Q22 — Prioritize control improvements

**Question:** Which controls should Internal Audit prioritise in 2026 if it wants to reduce the most material customer, compliance, payment, fraud, and technology risks?

**Evaluation comment:** Tests aggregation across audit planning and risk materiality. Use the risk-based audit priorities and 2026 plan in `governance/internal_audit_charter.md`, the risk appetite/KRIs in `risk_compliance/risk_appetite_statement_2026.md`, findings in `risk_compliance/audit_report_q4_2025.md`, and oversight in `governance/board_risk_committee_charter.md`. A good answer explains its prioritisation, not merely lists documents.

### Q23 — Build a compliant customer response after an incident

**Question:** Following a data breach, what must our customer communication include, when must it be sent, and which teams need to review it before release?

**Evaluation comment:** Tests incident-to-communications traversal. Use the customer-notification material and escalation/ownership in `reference/incident_response_plan.md`, privacy obligations in `policies/policy_privacy.md`, approved wording/resources in `templates/email_templates.md` and `templates/sms_templates.md`, and organisational roles in `organization/departments.md`.

### Q24 — Who owns a customer-impacting technology issue?

**Question:** A recurring online-banking error causes incorrect balances and complaints. Who owns the technical fix, who owns the customer response, and who should be kept informed if the issue is material?

**Evaluation comment:** Tests ownership resolution across `reference/technology_application_landscape.md`, `organization/organisation_model.md`, `organization/departments.md`, `reference/incident_response_plan.md`, `policies/policy_complaints_v3.md`, and `governance/board_risk_committee_charter.md`. The answer should distinguish operational accountability from technical ownership and Board-level oversight rather than naming one generic “manager.”

### Q30 — Open Banking delivery with a residual exception

**Question:** Did FirstUK meet the Open Banking deadline? What was delivered, what limited gap remains, who owns it, and how should its status be described?

**Evaluation comment:** Tests delivery status without overclaiming compliance. Use `reference/open_banking_delivery_outcome_2026_07.md` and `regulations/regulatory_circulars_2026.md`; identify the 37-mandate exception, workaround, 2026-08-15 remediation, CTO ownership, and Board route.

### Q31 — Coordinate the CASE-2026-041 response

**Question:** Give an integrated action plan for CASE-2026-041 covering containment, customer complaints, privacy/retention, AML, and governance reporting.

**Evaluation comment:** Tests end-to-end traversal through the four `cases/case_2026_041_*.md` records and their linked incident, privacy, retention, AML, complaint, and governance documents. Preserve workstream ownership and distinguish immediate response from later review.

## E. Structured store + graph (hybrid)

These questions require both stores: numbers/records from the structured
tables in `structured/` (customers, vulnerable_customers, agents, complaints,
csat_scores — ingested via `artmind ingest sync --domain banking`) and
narrative/policy reasoning from the graph. A strong answer keeps the SQL fact
and the policy explanation distinct rather than blending them, and — matching
this corpus's broader evaluation ethos — discloses a tie or a thin sample
instead of manufacturing false precision.

### Q32 — Vulnerable customer with an unresolved complaint

**Question:** Which vulnerable customer currently has an open, unresolved complaint, and what extra care does our training and complaints guidance require when handling their case?

**Evaluation comment:** Tests resolving a structured record to the right graph guidance. Joining `structured/complaints.csv` (`status = 'Open'`) against `structured/vulnerable_customers.csv` on `customer_id` surfaces exactly one match: CUST-0019, driver `Life Events`, support need `Safe Space`, complaint category `Service`. The answer should combine `training/branch_operations_training.md`'s vulnerable-customer guidance (abuse victims: offer a safe space, confidential help) with `policies/policy_complaints_v3.md`'s timeframe/escalation rule for a Service complaint, not generic vulnerable-customer platitudes.

### Q33 — Escalation authority for the largest Account Takeover payout

**Question:** What is the largest compensation amount recorded for an Account Takeover complaint, and per the complaint escalation matrix, what authority level and timeline would that payout require?

**Evaluation comment:** Tests a resolve-then-govern chain, mirroring the reasoning in Q07–Q09 but sourced from real transaction data instead of a hypothetical figure. `structured/complaints.csv` filtered to `category = 'Account Takeover'` gives two rows — CMP-0018 (£600) and CMP-0008 (£500) — so the largest is unambiguous. The answer should then apply `policies/policy_complaints_v3.md`'s (or `policy_complaints.md`'s, if the decision date requires the earlier version) escalation-by-loss table: payouts above £500 require Director / Head of Customer Service approval within 8 days, not frontline sign-off.

### Q34 — Fee-dispute customer satisfaction versus the policy remedy

**Question:** For customers who raised a Fee Dispute complaint on their SmartSaver Account, what is their average CSAT score, and what does the complaints policy require when a fee was applied correctly versus in error?

**Evaluation comment:** Tests keeping a SQL aggregate and a two-branch policy answer distinct rather than merging them. Joining `structured/complaints.csv` (`category = 'Fee Dispute'`, `product = 'SmartSaver Account'`) to `structured/customers.csv` and `structured/csat_scores.csv` covers four customers (CUST-0001, CUST-0009, CUST-0014, CUST-0021) with six survey rows. The policy answer should separately state: fee applied correctly per T&Cs → explain and decline reversal; fee error → reverse charge + interest (`policies/policy_complaints.md` / `policy_complaints_v3.md`, Fee Disputes).

### Q35 — Branch manager vulnerable-customer review load

**Question:** Which branch manager has reviewed the most vulnerable-customer cases on record, and according to branch operations training, what should they be doing differently depending on each case's vulnerability driver?

**Evaluation comment:** Tests refusing false precision. `structured/vulnerable_customers.csv` grouped by `reviewed_by` shows seven different branch managers, each with exactly one case — there is no single "busiest" reviewer, and a good answer says so instead of naming one arbitrarily. It should then map each represented driver to `training/branch_operations_training.md`'s per-driver guidance: Health/Capability (elderly or mentally incapacitated) → slow down, be patient; Capability (language barriers) → arrange an interpreter; Life Events (abuse victims) → offer a safe space; Resilience (financial abuse) → escalate to compliance.

### Q36 — CSAT gap between open and resolved complaints

**Question:** Do customers with an open, unresolved complaint show a different average CSAT score from customers whose complaints have already been resolved, and what does the complaints policy say should happen if a response is at risk of running late?

**Evaluation comment:** Tests not overclaiming from a thin sample — an intentional parallel to Q28's "distinguish, don't collapse" pattern. Only two customers have an `Open` complaint in `structured/complaints.csv` (CUST-0019, CUST-0024), and only CUST-0019 has any `structured/csat_scores.csv` rows (a single survey, score 3) versus 25 CSAT rows across resolved-complaint customers (avg. ~3.6) — a good answer flags the n=1 comparison as too thin to generalise rather than asserting a trend, and notes CUST-0024 has no CSAT record at all. It should then use `policies/policy_complaints.md` / `policy_complaints_v3.md`'s 8-day/8-week response clock and escalation rule for at-risk responses.

## Optional corpus enhancements for future benchmark rounds

These are recommendations, not evidence assumed by the questions above.

1. **Dated SOP change pack:** a revised account-opening or KYC SOP, change notice, approved training record, and implementation log linked to the enhanced-KYC circular.
2. **Independent monthly rate documents:** separate, explicitly superseding rate schedules for consecutive months rather than a single schedule with a history table.
3. **Case-event timeline:** a fictional complaint or fraud case spanning intake, investigation, system evidence, communications, escalation, and resolution.
4. **Unresolved factual discrepancy:** two credible contemporaneous sources with different customer-impact counts and no final adjudication, to test transparent conflict reporting.
5. **Regulatory implementation artefacts:** owner/action register, revised procedure approval, and assurance evidence bridging circulars and board reporting.
6. **Document-authority register:** authority order and status vocabulary (`draft`, `active`, `superseded`, `withdrawn`) for every controlled document.
7. **Retention/erasure case file:** a GDPR request tied to AML or litigation hold, including the final customer response and review record.
8. **Post-deadline Open Banking outcome:** a June/July delivery-status report with affected systems, any exceptions, owners, and Board reporting.
