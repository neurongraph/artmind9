# FirstUK Bank — Regulatory Circulars 2026

## Metadata

| Field | Value |
|-------|-------|
| Document ID | REG-CIRC-2026 |
| Version | 1.0 (as of Q1 2026) |
| Effective Date | 2026-01-15 |
| Review Date | Quarterly |
| Owner | Head of Compliance |
| Department | Compliance |
| Status | Active (Updated Quarterly) |
| Classification | Internal |
| Audience | Compliance, Risk, Executive, All Staff |
| Related Documents | [[policy_customer_identification]], [[policy_aml]], [[policy_privacy]] |

---

## Purpose

This document consolidates FirstUK Bank's tracking and implementation of regulatory updates from FCA (Financial Conduct Authority) and PRA (Prudential Regulation Authority) for 2026. Each quarter includes summary of regulatory changes, impact assessment, and implementation status.

---

## Q1 2026 (January–March) Regulatory Circulars

### Source: FCA

#### Circular: PSD2 Open Banking Deadline Extension

**FCA Reference:** [FCA-OP-2026-01]  
**Date Issued:** 2026-01-10  
**Effective:** 2026-06-30  
**Scope:** Payment Service Providers must provide open API access  

**Summary:**
FCA extended open banking deadline from March 2026 to June 2026 for non-large firms. FirstUK Bank is not classified as "large firm" (>£1B) but chooses to implement by June 2026 per strategic roadmap.

**Impact on FirstUK Bank:**
- **Medium Impact** — Requires API development for third-party access
- **Status:** In progress (see [[systems]] Integration Specification INT-SPEC-001)
- **Deadline:** June 30, 2026
- **Owner:** Chief Technology Officer
- **Action:** Continued API development, security testing, regulatory liaison

**Regulatory Requirements:**
- API must provide: Accounts, Transactions, Standing Orders, Direct Debits
- API must use OAuth 2.0 authentication
- Customer consent required before data sharing
- Data held for max 90 days (unless renewed consent)

---

#### Circular: GDPR Guidance on Data Subject Rights

**FCA Reference:** [ICO-GUID-2026-02]  
**Date Issued:** 2026-01-15  
**Effective:** Immediate  
**Scope:** Data protection for all organizations  

**Summary:**
ICO (Information Commissioner's Office) updated guidance on Subject Access Request (SAR) processing, particularly right to erasure ("right to be forgotten") and clarification of exceptions.

**Impact on FirstUK Bank:**
- **Low Impact** — FirstUK Bank already compliant with GDPR
- **Status:** Implemented (see [[policy_privacy]])
- **Update Required:** Clarify erasure exceptions in procedures
- **Owner:** Data Protection Officer
- **Action:** Update SAR procedures, staff training on new guidance

**Key Updates:**
- Erasure requests: Can be denied if regulatory hold (AML, etc.)
- SAR response: Must be in "commonly used electronic format"
- Fee: Cannot charge unless request "manifestly unfounded"
- Timeliness: 30 days (extendable to 60 for complex requests)

---

#### Circular: Enhanced Know Your Customer (KYC) for High-Risk Customers

**FCA Reference:** [FCA-COBS-2026-03]  
**Date Issued:** 2026-01-20  
**Effective:** 2026-03-01  
**Scope:** Customer Identification and Anti-Money Laundering  

**Summary:**
FCA issued enhanced guidance on Enhanced Due Diligence (EDD) for high-risk customers, with emphasis on beneficial ownership verification and source of funds.

**Impact on FirstUK Bank:**
- **High Impact** — Affects customer onboarding processes
- **Status:** Under review
- **Deadline:** March 1, 2026 (compliance date)
- **Owner:** Head of Compliance + Head of Financial Crime
- **Action:** Update KYC procedures, staff training, system changes (if needed)

**New Requirements:**
- Beneficial ownership: If customer is entity, must identify all beneficial owners (>25% ownership)
- Source of funds: Required for initial deposits >£10,000
- Enhanced screening: Multiple databases (OFAC, EU, UN, HMT, PEP)
- Enhanced ongoing monitoring: High-risk customers reviewed quarterly (vs. annually)

**FirstUK Bank Changes:**
- Update Account Opening SOP (ACCT-OPEN-SOP-001)
- Implement source of funds verification workflow
- Increase beneficial ownership documentation requirements
- Staff training (completion by March 1, 2026)

---

#### Circular: Technology Risk & Cybersecurity Standards

**FCA Reference:** [FCA-OPER-2026-04]  
**Date Issued:** 2026-01-25  
**Effective:** Immediate (best practice)  
**Scope:** Technology and Operational Resilience  

**Summary:**
FCA updated Operational Resilience guidance, emphasizing impact tolerance (how long customers can tolerate disruption) and scenario-based stress testing.

**Impact on FirstUK Bank:**
- **Medium Impact** — Already operating with high reliability standards
- **Status:** Implemented (see [[systems]] SLAs)
- **Update Required:** Formalize impact tolerance levels
- **Owner:** Chief Technology Officer + Chief Risk Officer
- **Action:** Document impact tolerance, stress test scenarios

**Key Updates:**
- Impact Tolerance: Define acceptable disruption duration by service (RTO)
- Scenario Testing: Test against realistic disruption scenarios
- Incident Response: Faster escalation and communication requirements
- Third-Party Risk: Enhanced vendor cybersecurity requirements

**FirstUK Bank Current State:**
- Online Banking RTO: 2 hours (aligned with guidance)
- Payment Processing RTO: 30 minutes (exceeds guidance)
- Scenario testing: Annual (compliant)

---

### Source: PRA

#### Circular: Capital Adequacy Ratios

**PRA Reference:** [PRA-CAP-2026-01]  
**Date Issued:** 2026-01-12  
**Effective:** 2026-02-01  
**Scope:** Small Firms (pillar 2)  

**Summary:**
PRA updated capital requirements for small firms, with slight increase in risk weightings for unsecured lending (mortgages).

**Impact on FirstUK Bank:**
- **Medium Impact** — Affects capital planning
- **Status:** Monitoring
- **Current Capital Ratio:** 18.2% (target: >15%)
- **Owner:** Chief Financial Officer
- **Action:** Recalculate capital ratios, adjust lending if needed

**Changes:**
- Mortgage risk weight: 25% → 30% (unsecured lending increase)
- Savings products: No change (0% risk weight)
- Impact on FirstUK Bank: Capital ratio reduced from 18.2% to 17.8%
- Still above minimum 15% requirement (comfortable buffer)

---

## Q2 2026 (April–June) Regulatory Circulars

**Pending:** To be updated end of March 2026

*Anticipated Topics:*
- Open Banking deadline implementation support
- Summer stress testing scenarios
- Brexit-related regulatory updates (if applicable)

---

## Q3 2026 (July–September) Regulatory Circulars

**Pending:** To be updated end of June 2026

---

## Q4 2026 (October–December) Regulatory Circulars

**Pending:** To be updated end of September 2026

---

## Implementation Tracking

### Summary Table (As of Q1 2026)

| Circular | Topic | Impact | Deadline | Status | Owner |
|----------|-------|--------|----------|--------|-------|
| FCA-OP-2026-01 | Open Banking API | Medium | 2026-06-30 | In Progress | CTO |
| ICO-GUID-2026-02 | GDPR Data Rights | Low | Immediate | Compliant | DPO |
| FCA-COBS-2026-03 | KYC/EDD | High | 2026-03-01 | Under Review | Compliance |
| FCA-OPER-2026-04 | Operational Resilience | Medium | Immediate | Compliant | CTO/CRO |
| PRA-CAP-2026-01 | Capital Ratios | Medium | 2026-02-01 | Compliant | CFO |

---

## Regulatory Contacts

**FCA (Financial Conduct Authority)**
- Website: www.fca.org.uk
- Supervisory Contact: [FirstUK Bank assigned supervisor]
- Updates: Via FCA portal + email subscription

**PRA (Prudential Regulation Authority)**
- Website: www.bankofengland.co.uk/pra
- Supervisory Contact: [FirstUK Bank assigned supervisor]
- Updates: Via PRA portal + email subscription

**ICO (Information Commissioner's Office)**
- Website: www.ico.org.uk
- Data Protection Guidance: www.ico.org.uk/for-organisations
- Queries: enquiries@ico.org.uk

---

## Related Documents

- [[policy_customer_identification]] — KYC Policy (affected by FCA-COBS-2026-03)
- [[policy_privacy]] — Privacy/GDPR Policy (affected by ICO-GUID-2026-02)
- [[systems]] — Technology Architecture (affected by FCA-OPER-2026-04)
- Regulatory Compliance Tracker (detailed implementation)

---

## Document Version History

| Quarter | Version | Changes | Date |
|---------|---------|---------|------|
| Q1 2026 | 1.0 | Initial Q1 circulars | 2026-01-15 |
| Q2 2026 | 2.0 (Pending) | Q2 circulars to be added | 2026-04-01 |
| Q3 2026 | 3.0 (Pending) | Q3 circulars to be added | 2026-07-01 |
| Q4 2026 | 4.0 (Pending) | Q4 circulars to be added | 2026-10-01 |

---

## Sign-Off

**Prepared by:**  
Head of Compliance — **Date: 2026-01-15**

**Approved by:**  
Chief Risk Officer — **Date: 2026-01-15**  
Chief Executive Officer — **Date: 2026-01-15**

---
