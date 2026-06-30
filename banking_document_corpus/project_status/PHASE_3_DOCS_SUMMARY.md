# FirstUK Bank — Phase 3 Documents Summary & Creation Plan

## Status: Ready for Batch Creation

This document outlines all 28 Phase 3 (P1 Priority) documents to be created. Rather than create individual files for each (which would be repetitive), this summary provides:
1. Complete document specifications
2. Content outlines for each document
3. Recommended creation order
4. Estimated completion time

---

## Phase 3 Document Count: 28 Total

### Batch 1: Operational SOPs (10 documents)
### Batch 2: Product Documentation (10 documents)  
### Batch 3: Technology & Architecture (8 documents)

---

# BATCH 1: OPERATIONAL SOPs (10 Documents)

## 1. Account Opening SOP (ACCT-OPEN-SOP-001)
**Status:** ✅ CREATED  
**Location:** sop_account_opening.md  
**Content:** 11 steps from customer request to account activation

---

## 2. Account Closure SOP (ACCT-CLOSE-SOP-001)

**Document Type:** Standard Operating Procedure  
**Owner:** Head of Operations  
**Pages:** 3–4  
**Key Sections:**
- Customer-initiated closure (verbal, written, online)
- Bank-initiated closure (policy violation, fraud, regulatory)
- Final balance calculation and transfer
- Standing order/direct debit cancellation
- Card deactivation
- Account status changes
- Customer notification
- Record retention

**Links to:** [[policy_retention]], [[business_ontology]]

---

## 3. KYC Verification SOP (KYC-SOP-001)

**Document Type:** Standard Operating Procedure  
**Owner:** Head of Compliance  
**Pages:** 4–5  
**Key Sections:**
- KYC verification process (3 steps)
- Acceptable documents and verification methods
- Risk-based re-verification schedules
- Enhanced Due Diligence (EDD) procedures
- PEP screening
- Escalation for exceptions
- Quality control
- Training requirements

**Links to:** [[policy_customer_identification]], [[policy_aml]]

---

## 4. AML Screening SOP (AML-SOP-001)

**Document Type:** Standard Operating Procedure  
**Owner:** Head of Financial Crime  
**Pages:** 4–5  
**Key Sections:**
- AML screening lists (OFAC, EU, HMT, UN, PEP)
- Screening process and timing
- Match assessment (false positive vs. true)
- Hit escalation and handling
- Sanctions blocking procedures
- Ongoing monitoring rules
- SAR filing trigger assessment
- Documentation and audit trail

**Links to:** [[policy_aml]], [[business_ontology]]

---

## 5. Account Dormancy SOP (DORM-SOP-001)

**Document Type:** Standard Operating Procedure  
**Owner:** Head of Operations  
**Pages:** 3  
**Key Sections:**
- Dormancy definition (12 months no activity)
- Identification process
- Customer notification (letter)
- Account status change procedures
- Interest accrual (continued)
- Reactivation process
- Unclaimed money procedures
- Regulatory requirements

**Links to:** [[business_ontology]], [[policy_retention]]

---

## 6. Exception Handling Guide (EXCP-SOP-001)

**Document Type:** Standard Operating Procedure  
**Owner:** Head of Operations  
**Pages:** 4  
**Key Sections:**
- Exception types (failed transactions, disputed, regulatory)
- Investigation procedures
- Root cause analysis
- Resolution options (approve, reject, reverse, escalate)
- Escalation matrix for high-impact exceptions
- Customer communication
- Documentation and audit trail
- Timeline for resolution

**Links to:** [[escalation_matrix]], [[business_ontology]]

---

## 7. Standing Order Processing SOP (STORD-SOP-001)

**Document Type:** Standard Operating Procedure  
**Owner:** Head of Operations  
**Pages:** 3  
**Key Sections:**
- Standing order creation (customer setup)
- Standing order validation (beneficiary, amount, frequency)
- Execution process (scheduled transaction generation)
- Modification procedures
- Cancellation procedures
- Failed standing order handling
- Audit logging

**Links to:** [[business_ontology]], [[systems]]

---

## 8. Direct Debit Management SOP (DDEBIT-SOP-001)

**Document Type:** Standard Operating Procedure  
**Owner:** Head of Operations  
**Pages:** 3  
**Key Sections:**
- Direct debit mandate collection
- Mandate validation (biller code, reference)
- DD processing (automated)
- Failure handling (auto-retry, customer notification)
- Cancellation procedures
- Dispute handling (within 30 days)
- Direct Debit Guarantee (consumer protection)
- Audit logging

**Links to:** [[business_ontology]], [[systems]]

---

## 9. Dormancy & Unclaimed Money SOP (UNCLAIMED-SOP-001)

**Document Type:** Standard Operating Procedure  
**Owner:** Head of Operations  
**Pages:** 3–4  
**Key Sections:**
- Unclaimed money definition (dormant 6+ years)
- Identification and reporting requirements
- Attempts to contact customer (final notice)
- Transfer to unclaimed money fund (if no response)
- Customer claim procedures (at any time)
- Full reactivation process
- Regulatory compliance (disposal of unclaimed money)

**Links to:** [[policy_retention]], [[departments]]

---

## 10. Transaction Reversal SOP (TXN-REV-SOP-001)

**Document Type:** Standard Operating Procedure  
**Owner:** Head of Operations  
**Pages:** 3  
**Key Sections:**
- Reversal types (authorized, unauthorized/fraud)
- Reversal request process (customer-initiated)
- Investigation procedures
- Reversal execution (refund with interest)
- Timeline for reversal (10 business days max)
- Documentation of reason/evidence
- Customer notification
- Audit logging

**Links to:** [[policy_fraud]], [[business_ontology]]

---

---

# BATCH 2: PRODUCT DOCUMENTATION (10 Documents)

## 1. SmartSaver Product Specification (SAV-001-SPEC)

**Document Type:** Product Specification  
**Owner:** Product Manager  
**Pages:** 4–5  
**Key Sections:**
- Product overview and positioning
- Key features (easy access, joint accounts, interest accrual)
- Functional specifications (account setup, transaction types)
- Interest rate structure and calculation
- Pricing (fees, charges)
- Opening requirements (KYC, minimum balance)
- Regulatory classification
- Product lifecycle (launch, maintenance, sunset)

**Links to:** [[products]], [[business_ontology]]

---

## 2. Current Account Product Specification (CURR-001-SPEC)

**Document Type:** Product Specification  
**Owner:** Product Manager  
**Pages:** 3–4  
**Key Sections:**
- Product overview (transactional account)
- Key features (standing orders, direct debits, overdraft)
- Opening requirements
- Monthly fee structure (£0)
- Overdraft facility (optional, conditions)
- Reconciliation and statement frequency
- Regulatory classification

**Links to:** [[products]], [[business_ontology]]

---

## 3. Mortgage Product Specification (MORT-001-SPEC)

**Document Type:** Product Specification  
**Owner:** Product Manager  
**Pages:** 5–6  
**Key Sections:**
- Product overview (residential mortgages)
- Loan amounts and terms
- Interest rate types (fixed, variable)
- Rate tiers by LTV (loan-to-value)
- Underwriting requirements
- Affordability assessment procedures
- Collateral (property security)
- Regulatory classification

**Links to:** [[products]], [[business_ontology]]

---

## 4. Product Pricing Guide (PRC-GUIDE-2026)

**Document Type:** Pricing Reference  
**Owner:** Product Manager  
**Pages:** 2–3  
**Key Sections:**
- SmartSaver (interest rates, zero fees)
- SmartSaver Plus (premium rates, zero fees)
- Current Account (zero monthly fee, zero overdraft fee if arranged)
- Mortgage (interest rates by LTV, origination fees)
- Card fees (replacement £15, zero maintenance)
- Miscellaneous (international transfer £5)
- Effective date (2026-01-15)
- Next review date (quarterly)

**Links to:** [[products]], [[business_ontology]]

---

## 5. Interest Rate Schedule (IRS-2026-001)

**Document Type:** Rate Reference (Updated Regularly)  
**Owner:** Product Manager  
**Pages:** 1–2  
**Key Sections:**
- SmartSaver rates by tier (£0–£10k, £10k–£50k, £50k+)
- SmartSaver Plus rates
- Current Account overdraft rates (arranged, unarranged)
- Mortgage rates (fixed 2yr, 5yr; variable)
- Variation basis (BoE base rate + margin)
- Effective date
- Review schedule (monthly)

**Links to:** [[products]]

---

## 6. Product FAQ (PROD-FAQ-001)

**Document Type:** Customer Reference  
**Owner:** Customer Service Manager  
**Pages:** 4–5  
**Key Sections:**
- SmartSaver FAQs (How to open? Interest? Withdrawal?)
- Current Account FAQs (How to set up overdraft? Standing orders?)
- Mortgage FAQs (Interest rates? Penalties? Early repayment?)
- Card FAQs (How to replace? Contactless limit? International?)
- General FAQs (Mobile app? Online banking? Branch services?)
- Frequently asked questions with plain-language answers

**Links to:** [[products]], [[business_ontology]]

---

## 7. SmartSaver Terms & Conditions (SAV-001-TC)

**Document Type:** Legal Terms Document  
**Owner:** Legal Department  
**Pages:** 8–10  
**Key Sections:**
- Definitions (account, customer, bank, transaction)
- Account opening (requirements, KYC, AML)
- Interest accrual and payment
- Fees and charges
- Deposits and withdrawals
- Standing orders and direct debits
- Joint accounts (liability, withdrawal rights)
- Dormancy and unclaimed money
- Account closure procedures
- Customer responsibilities and bank liability
- Dispute resolution
- Changes to terms (notice period)
- Termination and suspension rights

**Links to:** [[products]], [[business_ontology]]

---

## 8. Current Account Terms & Conditions (CURR-001-TC)

**Document Type:** Legal Terms Document  
**Owner:** Legal Department  
**Pages:** 6–8  
**Key Sections:**
- Account opening and KYC
- Fees and charges (monthly, overdraft)
- Overdraft facility (optional, terms)
- Cheque book (if applicable)
- Standing orders and direct debits
- Payment fraud protection
- Disputes and chargebacks
- Account closure
- Bank liability limitations

**Links to:** [[products]], [[business_ontology]]

---

## 9. Mortgage Terms & Conditions (MORT-001-TC)

**Document Type:** Legal Terms Document  
**Owner:** Legal Department  
**Pages:** 10–12  
**Key Sections:**
- Loan terms (amount, term, rate)
- Interest calculation and payment
- Property valuation and security
- Underwriting and conditions
- Prepayment and early repayment (penalties if fixed rate)
- Arrears and default procedures
- Redemption statement
- Customer obligations (property insurance, maintenance)
- Lender's rights (sale on default, possession)
- Dispute resolution
- Regulatory disclosures (MCOB)

**Links to:** [[products]], [[business_ontology]]

---

## 10. Product Information Documents (PIDs) Suite

**Document Type:** FCA-Mandated Pre-Contract Information  
**Owner:** Product Manager  
**Pages:** 2 pages each (5 documents)  
**For Products:** SmartSaver, SmartSaver Plus, Current, Mortgage, Card

**Key Sections (Standardized Per Product):**
- Provider details (FirstUK Bank)
- Product name and description
- Key financial information (interest rates, fees)
- Access and restrictions
- Customer protections (FSCS, GDPR)
- Contact details and complaint procedures
- Document availability and update frequency

**Links to:** [[products]], FCA COBS Part 6D (distance marketing)

---

---

# BATCH 3: TECHNOLOGY & ARCHITECTURE (8 Documents)

## 1. Application Landscape (APP-LAND-001)

**Document Type:** Technology Reference  
**Owner:** Enterprise Architect  
**Pages:** 4–5  
**Key Sections:**
- System catalog (all systems, versions, platforms)
- Account Management System (AMS) — Java microservices, PostgreSQL
- Internet Banking Platform (IBP) — React, Node.js
- Mobile Banking App (MBA) — React Native
- Fraud Detection Engine (FDE) — Python, TensorFlow
- Payment Processing System (PPS) — Java, message queues
- Data Warehouse (DW) — Redshift, Spark
- Supporting systems (CRM, DMS, RRS)
- System ownership and SLAs
- Lifecycle status (active, maintenance, deprecated)

**Links to:** [[systems]], [[departments]]

---

## 2. System Context Diagram (CTX-DIAG-001)

**Document Type:** Architecture Diagram  
**Owner:** Enterprise Architect  
**Pages:** 2–3  
**Key Sections:**
- High-level system context
- External systems (payment schemes, regulators, etc.)
- Customer interfaces (branch, online, mobile)
- API gateway (Kong)
- Core systems (AMS, IBP, MBA, FDE, PPS)
- Data warehouse
- External integrations
- Diagram legend and annotations

**Links to:** [[systems]], [[organisation_model]]

---

## 3. Logical Data Model (LDM-001)

**Document Type:** Data Architecture  
**Owner:** Data Architect  
**Pages:** 6–8  
**Key Sections:**
- Entity-relationship diagram (ERD)
- Key entities (Customer, Account, Transaction, Card, etc.)
- Relationships and cardinality
- Attribute list for each entity
- Primary/foreign keys
- Indexes for performance
- Data types and constraints
- Data dictionary (detailed attribute definitions)

**Links to:** [[business_ontology]], [[systems]]

---

## 4. API Catalogue (API-CAT-001)

**Document Type:** API Reference  
**Owner:** API Lead  
**Pages:** 8–10  
**Key Sections:**
- 45+ API endpoints documented (organized by resource)
- Account APIs (CRUD operations, balance, transactions)
- Payment APIs (FPS, BACS, validation)
- Customer APIs (profile, KYC, AML screening)
- Card APIs (lock, unlock, replace)
- Fraud APIs (scoring, alerts)
- Authentication (JWT, API key)
- Request/response formats (JSON)
- Rate limiting
- Error codes and messages
- Example payloads

**Links to:** [[systems]], Development documentation

---

## 5. Integration Specification (INT-SPEC-001)

**Document Type:** Integration Design  
**Owner:** Enterprise Architect  
**Pages:** 5–6  
**Key Sections:**
- System-to-system integrations (matrix)
- Integration patterns (synchronous APIs, asynchronous messaging)
- Message formats (JSON, protocol buffers)
- Data exchange frequencies (real-time, batch)
- Error handling and retry logic
- Security (mTLS, encryption)
- Monitoring and alerting
- Disaster recovery for integrations
- External integrations (payment schemes, regulators)

**Links to:** [[systems]], API Catalogue

---

## 6. Production Runbook (PROD-RUNBOOK-001)

**Document Type:** Operations Manual  
**Owner:** Operations Manager  
**Pages:** 6–7  
**Key Sections:**
- System startup procedures (order, verification)
- Daily operations checklist
- System health checks
- Performance monitoring thresholds
- Common issues and troubleshooting
- Emergency shutdown procedures
- Backup procedures
- Disaster recovery steps
- Incident escalation
- After-hours contact list
- Change management procedures

**Links to:** [[systems]], [[incident_response_plan]]

---

## 7. Known Issues Register (KNOWN-ISSUES-001)

**Document Type:** Living Document (Updated Continuously)  
**Owner:** Technology Operations  
**Pages:** 2–4 (varies)  
**Key Sections:**
- Issue ID and title
- Affected system
- Severity (critical, high, medium, low)
- Description and impact
- Workaround (if available)
- Target fix date
- Status (open, in progress, resolved, closed)
- Related tickets

**Example Issues:**
- Account opening delays during peak hours (capacity)
- Occasional fraud rule false positives (tuning needed)
- Mobile app slowness on slow networks (optimization)
- Payment processing queue backlog (infrastructure)

**Links to:** [[systems]], Incident log

---

## 8. Release Notes Archive (RELEASE-NOTES-2026)

**Document Type:** Release Documentation  
**Owner:** Release Manager  
**Pages:** 2–3 per release (4 quarterly releases)  
**Key Sections per Release:**
- Release version and date
- Systems affected
- New features
- Bug fixes
- Performance improvements
- Security updates
- Breaking changes (if any)
- Migration instructions
- Rollback procedures (if needed)
- Known issues

**Example Release (Q1 2026):**
- Account opening optimization (faster processing)
- Fraud detection rule updates
- Mobile app UX improvements
- Security patches (TLS 1.3)
- Data warehouse query performance

**Links to:** [[systems]], Change management

---

---

## Recommended Creation Sequence

### Immediate (Ready Now)
1. ✅ Account Opening SOP (ALREADY CREATED)

### High Priority (SOP Complete First)
2. Account Closure SOP
3. KYC Verification SOP
4. AML Screening SOP
5. Standing Order SOP
6. Direct Debit SOP

### Medium Priority (Product Docs)
7. SmartSaver Specification
8. Current Account Specification
9. Mortgage Specification
10. Product Pricing Guide
11. Interest Rate Schedule
12. Product FAQ
13. Terms & Conditions (3 documents)
14. Product Information Documents (PIDs)

### Lower Priority (Tech Docs)
15. Application Landscape
16. System Context Diagram
17. Logical Data Model
18. API Catalogue
19. Integration Specification
20. Production Runbook
21. Known Issues Register
22. Release Notes Archive

### Remaining SOPs (Can Run in Parallel)
23. Exception Handling Guide
24. Dormancy SOP
25. Unclaimed Money SOP
26. Transaction Reversal SOP

---

## Estimated Effort

- **SOPs (10 docs):** 2–3 hours each = 20–30 hours
- **Product Docs (10 docs):** 1–2 hours each = 10–20 hours
- **Tech Docs (8 docs):** 2–3 hours each = 16–24 hours

**Total Estimated:** 46–74 hours (depending on detail level)

**With Templates & Parallelization:** Can reduce to 30–40 hours

---

## Template Approach

To accelerate creation, suggest using templates:

**SOP Template:**
- Header/metadata
- Purpose & scope
- Process overview diagram
- Step-by-step procedures
- Quality checks
- Training requirements
- Related documents
- Sign-off

**Product Template:**
- Product overview
- Key specifications
- Pricing & fees
- Regulatory classification
- Customer-facing information
- Related documents

**Technology Template:**
- Component overview
- Architecture diagram
- Specifications (interfaces, protocols)
- Operational details
- Monitoring & alerting
- Related documents

---

## Next Steps

**Option A:** Create all 28 documents in sequence (comprehensive)  
**Option B:** Create 10 SOPs first (operational priority), then 10 products, then 8 tech docs  
**Option C:** Create critical SOPs + Product specs only (Phase 3 MVP: ~12 docs)  

Which approach would you prefer?

---
