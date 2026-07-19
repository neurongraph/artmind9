# Banking Corpus Extension Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an interlinked banking evidence pack and integrate its test prompts into the existing capability-based Q&A benchmark.

**Architecture:** New Markdown documents are added only; existing corpus documents remain unchanged. A controlled-change pack, independent dated rate schedules, an Open Banking outcome, and a CASE-2026-041 evidence pack link through metadata and wiki links. New benchmark prompts are placed in the existing temporal, conflict, and cross-cutting sections.

**Tech Stack:** Markdown, repository corpus documents, `rg`, `git`.

---

## File structure

- Create: `banking_document_corpus/governance/document_authority_register.md` — authority and status rules.
- Create: `banking_document_corpus/policies/policy_customer_identification_v2.md` — enhanced-KYC policy revision.
- Create: `banking_document_corpus/sop_procedures/sop_account_opening_v3.md` — revised onboarding procedure.
- Create: `banking_document_corpus/training/enhanced_kyc_training_completion_2026.md` — approved training evidence.
- Create: `banking_document_corpus/risk_compliance/enhanced_kyc_implementation_register.md` — regulatory implementation trace.
- Create: `banking_document_corpus/reference/interest_rate_schedule_2026_02.md` and `interest_rate_schedule_2026_03.md` — dated, superseding rate schedules.
- Create: `banking_document_corpus/reference/open_banking_delivery_outcome_2026_07.md` — post-deadline delivery status.
- Create: `banking_document_corpus/cases/case_2026_041_{overview,incident_timeline,complaint_record,retention_decision}.md` — shared case evidence and intentional 120/118 discrepancy.
- Modify: `banking_document_corpus/questions.md` — integrate new prompts into existing capability sections.

### Task 1: Add authority and KYC controlled-change documents

**Files:**
- Create: `banking_document_corpus/governance/document_authority_register.md`
- Create: `banking_document_corpus/policies/policy_customer_identification_v2.md`
- Create: `banking_document_corpus/sop_procedures/sop_account_opening_v3.md`
- Create: `banking_document_corpus/training/enhanced_kyc_training_completion_2026.md`
- Create: `banking_document_corpus/risk_compliance/enhanced_kyc_implementation_register.md`

- [ ] **Step 1: Write authority and status rules**

Create the register with precedence order, definitions for `draft`, `active`, `superseded`, and `withdrawn`, and resolution rules based on applicable date, authority, and explicit supersession. Link it to existing regulatory, policy, SOP, and governance material.

- [ ] **Step 2: Write the enhanced-KYC policy and SOP revisions**

Make the policy v2 and SOP v3 effective 2026-03-01. Link both to `FCA-COBS-2026-03`, explicitly supersede their existing versions, and require beneficial-owner verification above 25%, source-of-funds checks above £10,000, enhanced screening, and quarterly review for high-risk customers.

- [ ] **Step 3: Write completion and implementation evidence**

Record training completion before the deadline and an implementation register that traces every regulatory requirement to policy/SOP/training/system evidence, owner, date, and assurance status.

### Task 2: Add independent temporal documents

**Files:**
- Create: `banking_document_corpus/reference/interest_rate_schedule_2026_02.md`
- Create: `banking_document_corpus/reference/interest_rate_schedule_2026_03.md`
- Create: `banking_document_corpus/reference/open_banking_delivery_outcome_2026_07.md`

- [ ] **Step 1: Create February and March schedules**

Give every schedule its own effective date, predecessor/successor links, SmartSaver tier rates, review basis, and notification method. March must supersede February; February must supersede the January schedule.

- [ ] **Step 2: Create the Open Banking outcome report**

Report the 30 June 2026 delivery outcome, the customer-consent and data-retention controls, systems and owners, evidence of testing, a limited residual exception, its mitigation and Board reporting route.

### Task 3: Add CASE-2026-041 evidence pack

**Files:**
- Create: `banking_document_corpus/cases/case_2026_041_overview.md`
- Create: `banking_document_corpus/cases/case_2026_041_incident_timeline.md`
- Create: `banking_document_corpus/cases/case_2026_041_complaint_record.md`
- Create: `banking_document_corpus/cases/case_2026_041_retention_decision.md`

- [ ] **Step 1: Create the case overview and incident timeline**

Use the shared identifier `CASE-2026-041`. Record a data incident with 120 potentially affected customers, status `Open`, technical remediation, governance escalation, and a timeline that later reports 118 confirmed customers while explicitly leaving reconciliation open.

- [ ] **Step 2: Create the complaint and retention decision**

Record a representative customer complaint, data-subject access/erasure request, AML/legal hold, limited disclosure decision, customer communication, and review date. Link both documents to the overview and timeline without resolving the 120/118 discrepancy.

### Task 4: Integrate and validate questions

**Files:**
- Modify: `banking_document_corpus/questions.md`

- [ ] **Step 1: Add temporal-authority prompts to section B**

Add prompts on authority precedence, enhanced-KYC effective date and implementation, and rate selection across February/March. Every prompt uses the existing heading/question/evaluation-comment format.

- [ ] **Step 2: Add conflict-and-constraint prompts to section C**

Add prompts on CASE-2026-041’s 120/118 difference and the erasure request under AML/legal hold. Evaluator comments must require attribution and non-resolution of the discrepancy.

- [ ] **Step 3: Add cross-cutting prompts to section D**

Add prompts on the Open Banking delivery exception and the case’s technical, customer, privacy, risk, and governance action chain.

- [ ] **Step 4: Verify scope, links, and benchmark pairing**

Run:

```bash
rg -n '^### Q|^\*\*Evaluation comment:\*\*' banking_document_corpus/questions.md
rg -n '\[\[' banking_document_corpus/{cases,governance,policies,sop_procedures,training,risk_compliance,reference} --glob '*.md'
git diff --check
```

Expected: every question has one evaluator comment, all new documents contain related-document links, and no Markdown whitespace errors appear.

- [ ] **Step 5: Review and commit only intended content**

Run:

```bash
git status --short
git diff -- banking_document_corpus/questions.md banking_document_corpus/cases
git add banking_document_corpus docs/superpowers/plans/2026-07-19-banking-corpus-extension.md
git commit -m "docs: extend banking benchmark corpus"
```

Expected: the commit contains only new corpus documents, the questions update, and the implementation plan; it excludes unrelated worktree changes.

## Self-review

- Spec coverage: Tasks 1–3 create every agreed document group; Task 4 puts each new benchmark scenario in an existing functional section.
- Placeholder scan: no unfinished content is present in the plan.
- Consistency: CASE-2026-041 is the shared identifier; 120 is the potential-impact count, 118 is the confirmed-count reconciliation, and the difference remains open in every case document.
