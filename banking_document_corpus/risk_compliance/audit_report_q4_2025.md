# FirstUK Bank — Internal Audit Report Q4 2025

## Metadata

| Field | Value |
|-------|-------|
| Document ID | AUDIT-REPORT-Q4-2025 |
| Version | 1.0 |
| Report Period | October 1 – December 31, 2025 |
| Issued Date | 2026-01-15 |
| Review Date | 2026-04-15 |
| Owner | Head of Internal Audit |
| Department | Internal Audit |
| Status | Final |
| Classification | Confidential (Board, Audit Committee) |
| Audience | Board Audit Committee, Management, Risk Committee |
| Related Documents | [[internal_audit_charter]], [[board_risk_committee_charter]], [[escalation_matrix]], [[incident_response_plan]] |

---

## Executive Summary

**Period:** Q4 2025 (October 1 – December 31, 2025)  
**Audits Completed:** 6 major audits + 4 follow-ups  
**Overall Assessment:** SATISFACTORY (with observations)  
**Critical Findings:** 0  
**High Priority Findings:** 2  
**Medium Priority Findings:** 5  
**Low Priority Findings:** 3  

**Key Highlights:**
- ✅ AML screening controls operating effectively (100% compliance)
- ✅ KYC verification procedures meet FCA standards
- ⚠️ Standing order management has gaps in validation
- ⚠️ Account closure procedures missing documentation
- ✅ Fraud detection rates improved (97%+ detection)
- ✅ Technology infrastructure resilient (99.8% uptime)

---

## 1. AUDIT PLAN COMPLETION

### Planned Audits (6 of 6 Completed)

| Audit Name | Scope | Completion | Status |
|---|---|---|---|
| **AML Controls** | OFAC/EU/HMT screening compliance | Dec 2, 2025 | ✅ Complete |
| **KYC Verification** | Document verification procedures | Dec 9, 2025 | ✅ Complete |
| **Account Opening** | End-to-end onboarding process | Dec 16, 2025 | ✅ Complete |
| **Standing Orders** | Recurring payment controls | Dec 23, 2025 | ✅ Complete |
| **Account Closure** | Customer account termination | Jan 6, 2026 | ✅ Complete |
| **Fraud Detection** | FDE system effectiveness | Jan 13, 2026 | ✅ Complete |

### Follow-Up Audits (4 of 4 Completed)

Previous findings from Q3 2025:
- ✅ Payment processing delays (RESOLVED)
- ✅ System backup procedures (RESOLVED)
- ⚠️ Interest calculation accuracy (PARTIAL)
- ⚠️ Staff compliance training (PARTIAL)

---

## 2. KEY FINDINGS BY PROCESS

### A. AML Screening Controls — ✅ SATISFACTORY

**Scope:** OFAC/EU/HMT/UN sanctions screening against customer base  
**Period Tested:** Sept 1 – Dec 31, 2025  
**Sample Size:** 500 new customer accounts opened  
**Compliance Rate:** 100%

**Findings:**

**✅ Strengths:**
- All 500 new customers screened before account opening
- Screening times: <5 minutes (within SLA)
- False positive management effective (average 1.2% false positive rate)
- PEP screening added Q4 (improvement)
- No sanctions matches missed

**Key Metric:**
- Customers screened: 500 (100%)
- Sanctions matches: 0 (clean)
- False positives: 6 (1.2%)
- Processing time: Avg 3.2 min (SLA: 5 min) ✅

**Observation:**
All AML screening procedures operating per [[policy_aml]]. No exceptions noted.

---

### B. KYC Verification — ✅ SATISFACTORY (Minor Observation)

**Scope:** Know-Your-Customer document verification procedures  
**Period Tested:** Oct 1 – Dec 31, 2025  
**Accounts Tested:** 120 random sample of opened accounts  
**Compliance Rate:** 95%

**Findings:**

**✅ Strengths:**
- All accounts have ID on file (100%)
- Address verification 95% compliant
- Document quality checks effective
- No fraudulent documents detected

**⚠️ Observation (Low Priority):**
- 6 of 120 accounts (5%) missing proof of address
- Reason: Postal applications in-flight (awaiting documents)
- Root cause: Process delay, not control failure
- Impact: Accounts marked pending, transactions restricted appropriately

**Action Taken:**
- All 6 customers contacted, documents obtained within 30 days
- Processes per [[policy_customer_identification]] working correctly
- No remediation required

**Key Metric:**
- ID verification: 120/120 (100%) ✅
- Address verification: 114/120 (95%) ✅
- Fraudulent documents: 0 (0%)

---

### C. Account Opening Process — ✅ SATISFACTORY

**Scope:** End-to-end account opening (channel: online/branch/postal)  
**Period Tested:** Nov 15 – Dec 31, 2025  
**Accounts Tested:** 89 account openings  
**Timeliness:** Online avg 1.5 days | Branch avg same-day | Postal avg 6 days

**Findings:**

**✅ Strengths:**
- All 89 accounts followed [[sop_account_opening]] procedures
- Decision times within SLA
- Risk assessment applied correctly
- Regulatory holds (if any) documented

**Key Metric:**
| Channel | Count | Avg Time | SLA | Status |
|---|---|---|---|---|
| **Online** | 45 | 1.5 days | 2 days | ✅ |
| **Branch** | 32 | 4 hours | Same-day | ✅ |
| **Postal** | 12 | 6 days | 7 days | ✅ |

---

### D. Standing Orders Management — ⚠️ SATISFACTORY (Observation)

**Scope:** Recurring payment setup, execution, modifications, cancellations  
**Period Tested:** Oct 1 – Dec 31, 2025  
**Standing Orders Active:** 47 total orders  
**Orders Tested:** 20 sample (42% of active)  
**Compliance Rate:** 85%

**Findings:**

**✅ Strengths:**
- All standing orders established per [[sop_standing_orders]]
- Execution timing correct (95% on due date)
- Recipient validation working

**⚠️ Findings (Medium Priority - 2 issues):**

**Finding 1: Missing Modification Documentation**
- 3 of 20 orders (15%) had modifications without audit trail
- Example: Payment amount changed from £500 to £750, not documented
- Risk: Difficulty tracing changes for audit/dispute
- Impact: Medium (affects audit trail, not customer impact)

**Finding 2: Cancellation Delay**
- 2 of 20 orders (10%) took >1 day to cancel
- Customer requested cancellation; processed day after
- SLA: Immediate cancellation
- Impact: Low (only 1-day delay, manually corrected)

**Remediation Required:**
1. Document all modifications (audit trail)
2. Implement automated cancellation (effective immediately)
3. Retest Q1 2026

**Key Metric:**
- Standing orders created: 47 (100%) ✅
- Timely execution: 44/47 (95%) ✅
- Proper cancellations: 45/47 (96%) ✅
- Missing documentation: 3/47 (6%) ⚠️

---

### E. Account Closure — ⚠️ NEEDS IMPROVEMENT (Observation)

**Scope:** Customer-initiated and bank-initiated account closures  
**Period Tested:** Oct 1 – Dec 31, 2025  
**Closures Tested:** 15 sample closures  
**Compliance Rate:** 80%

**Findings:**

**✅ Strengths:**
- All closures processed per [[sop_account_closure]]
- Final balances calculated correctly
- Standing orders cancelled

**⚠️ Finding (High Priority):**

**Missing Documentation on Bank-Initiated Closures**
- 3 of 15 closures (20%) were bank-initiated
- Only 1 of 3 had proper 30-day notice letter on file
- Only 2 of 3 had documented reason (fraud, policy, regulatory)
- Impact: Regulatory risk (FCA may query closure justification)

**Remediation Required:**
1. Implement mandatory closure letter procedure
2. Document closure reason (dropdown: regulatory, fraud, policy, other)
3. Mandatory 30-day notice for bank-initiated closures
4. Audit trail for all closures
5. Retest Q1 2026

**Key Metric:**
- Customer-initiated closures: 12/12 (100%) ✅
- Bank-initiated closures: 3/15 (20%)
  - With proper notice: 1/3 (33%) ⚠️
  - With documented reason: 2/3 (67%) ⚠️

---

### F. Fraud Detection — ✅ EXCELLENT

**Scope:** Fraud Detection Engine (FDE) effectiveness  
**Period Tested:** Oct 1 – Dec 31, 2025  
**Transactions Analyzed:** 2,847 total transactions  
**Fraud Flags:** 89 transactions flagged  
**Confirmed Fraud:** 47 transactions  
**False Positives:** 39 transactions (44%)

**Findings:**

**✅ Strengths:**
- Detection rate: 97.9% (47 of 48 frauds caught; 1 missed)
- Average response time: 2.3 hours (excellent)
- Customer refunds processed within 10 days (100%)
- Model accuracy improved vs. Q3 (was 96%, now 97.9%)

**Key Metric:**
| Metric | Q3 2025 | Q4 2025 | Trend |
|---|---|---|---|
| **Detection Rate** | 96% | 97.9% | ↑ +1.9% ✅ |
| **False Positives** | 48% | 44% | ↓ -4% ✅ |
| **Response Time** | 2.8 hrs | 2.3 hrs | ↓ -0.5 hrs ✅ |
| **Frauds Prevented** | £34k | £47k | ↑ +39% ✅ |

**Notable Fraud Prevented:**
- Card-not-present transaction, unusual merchant, £2,500 charge → BLOCKED
- Multiple rapid international transfers → FLAGGED (investigation revealed fraudster)
- Account takeover attempt → CAUGHT within 15 minutes

---

## 3. TECHNOLOGY & INFRASTRUCTURE

### System Availability (✅ Exceeds Target)

**Target SLA:** 99.5% uptime  
**Actual Q4:** 99.87% uptime ✅

**Downtime Analysis:**
- Oct: 1 hr 20 min (scheduled maintenance)
- Nov: 45 min (network issue, auto-recovered)
- Dec: 22 min (peak traffic spike, auto-scaled)
- **Total Unplanned Downtime:** 45 minutes (0.01% of quarter)

**Impact:** No customer-facing incidents

---

### Backup & Disaster Recovery (✅ Operational)

**Backup Frequency:** Daily (nightly, 23:00 UTC)  
**Backup Location:** AWS S3 (encrypted, multi-region)  
**Recovery Testing:** Monthly restore test (all 3 months successful)  
**RTO:** 2 hours (tested, confirmed)  
**RPO:** 15 minutes (tested, confirmed)

**Status:** ✅ All backups successful, DR procedures validated

---

## 4. COMPLIANCE & REGULATORY

### FCA/PRA Alignment (✅ Compliant)

**Regulatory Framework Checklist:**

| Framework | Status | Notes |
|---|---|---|
| **COBS** (Conduct of Business) | ✅ Compliant | Customer communications clear, fair |
| **GDPR** | ✅ Compliant | Data handling per policy |
| **AML/CFT** | ✅ Compliant | Screening 100%, KYC 95% |
| **Operational Resilience** | ✅ In Progress | New requirements, on track |
| **POCA** | ✅ Compliant | Document retention on schedule |

**Status:** FirstUK Bank demonstrates strong regulatory compliance posture

---

### Staff Compliance Training (⚠️ Observation)

**Mandatory Compliance Training Completion:**
- Target: 100% annually
- Actual Q4: 94% (47 of 50 staff)
- 3 staff members outstanding (new hires, onboarding in progress)
- All 3 had compliance training scheduled by Jan 31, 2026

**Status:** Acceptable (tracking below 100%, but with valid reasons)

---

## 5. PROCESS IMPROVEMENTS IDENTIFIED

### High Priority (Address Q1 2026)

1. **Account Closure Documentation** (HIGH)
   - Implement mandatory closure letter procedure
   - Document closure reason in system
   - Expected completion: Feb 2026
   - Owner: Head of Operations

2. **Standing Order Audit Trail** (HIGH)
   - Log all modifications with timestamp/user
   - System enhancement required
   - Expected completion: March 2026
   - Owner: CTO

### Medium Priority (Address by Q2 2026)

3. **Interest Calculation Verification** (from Q3 follow-up)
   - Manual verification of interest calculations (10% sample)
   - Spot-check rate tiers and monthly calculations
   - Owner: Risk team

4. **Customer Communication Clarity** (Minor)
   - Simplify terms & conditions language
   - Improve rate change notification timing

---

## 6. MANAGEMENT RESPONSE

### Audit Findings Response

**Prepared by:** Head of Operations  
**Date:** January 12, 2026

**Accepted Findings:**
1. ✅ Standing order audit trail — Will implement by March 2026
2. ✅ Account closure documentation — Will implement by Feb 2026
3. ✅ Standing order cancellation delays — Will automate by Feb 2026

**Timelines Agreed:**
- Account closure improvements: 30 days (by Feb 15)
- Standing order audit trail: 60 days (by March 15)
- Interest calculation review: Ongoing (monthly spot checks start Jan 2026)

---

## 7. PRIOR PERIOD FOLLOW-UPS

### Q3 2025 Outstanding Items — Status Update

| Issue | Status | Resolution Date |
|---|---|---|
| **Payment Processing Delays** | ✅ Resolved | Dec 1, 2025 |
| **System Backup Procedures** | ✅ Resolved | Nov 15, 2025 |
| **Interest Calc Accuracy** | ⚠️ In Progress | Q1 2026 |
| **Staff Training Completion** | ⚠️ In Progress | Jan 31, 2026 |

**Summary:** 2 of 4 resolved; 2 remain in progress (acceptable timeline)

---

## 8. AUDIT OPINION & RECOMMENDATION

### Overall Assessment: SATISFACTORY

**Basis:**
- ✅ Major controls operating effectively (AML, KYC, Fraud detection)
- ✅ Regulatory compliance strong
- ⚠️ Minor process gaps identified (documentation, audit trail)
- ✅ Management responsive to findings

**Key Strengths:**
1. AML compliance exceptional (100%)
2. Fraud detection leading performance (97.9% detection)
3. System uptime excellent (99.87%)
4. Account opening process efficient and compliant

**Areas for Improvement:**
1. Account closure documentation (regulatory risk)
2. Standing order audit trail (compliance gap)
3. Staff training completion (compliance target)

### Recommendation

**PROCEED with minor remediation.**

Management should address the two high-priority findings (account closure documentation and standing order audit trail) by Q1 2026. Once remediated, FirstUK Bank's control environment will be STRONG.

---

## AUDITOR CERTIFICATION

This report reflects the independent assessment of FirstUK Bank's internal audit function based on procedures conducted during Q4 2025.

**Prepared by:** Sarah Mitchell, Internal Audit Manager  
**Reviewed by:** James Wilson, Head of Internal Audit  
**Approved by:** Board Audit Committee (approved Jan 15, 2026)

**Limitations:** Audit scope limited to processes tested; no opinion on processes not examined.

---

## Document Version History

| Version | Date | Change | Author |
|---------|------|--------|--------|
| 1.0 | 2026-01-15 | Q4 2025 Audit Report | Internal Audit |

---

**Confidential — Board Audit Committee Distribution Only**  
**Next Quarterly Report:** Q1 2026 (April 2026)
