# Phase 3: Complete Document List (27 Remaining + 1 Created)

## Legend
🔴 **CRITICAL** — Essential for operations, compliance, or customer delivery  
🟡 **IMPORTANT** — Valuable, but can wait for Phase 4  
🟢 **NICE-TO-HAVE** — Supplementary, lowest priority  
✅ **CREATED** — Already completed

---

# BATCH 1: OPERATIONAL SOPs (10 Documents)

## Overview
7 CRITICAL (incl. 1 created) | 3 IMPORTANT

### 1. Account Opening SOP (ACCT-OPEN-SOP-001)
**Status:** ✅ **CREATED**  
**Priority:** 🔴 CRITICAL  
**Why:** Core business process, regulatory requirement (KYC/AML)  
**Location:** sop_account_opening.md  
**Pages:** 11  

### 2. Account Closure SOP (ACCT-CLOSE-SOP-001)
**Priority:** 🔴 CRITICAL  
**Why:** Opposite of account opening, affects customer retention, regulatory record-keeping  
**Content:** Customer-initiated/bank-initiated closure, final balance, standing order cancellation  
**Pages:** 3–4  

### 3. KYC Verification SOP (KYC-SOP-001)
**Priority:** 🔴 CRITICAL  
**Why:** Regulatory blocker—FCA requirement, operational standard for every account  
**Content:** 3-step verification (document collection, physical/digital verification, system recording)  
**Pages:** 4–5  

### 4. AML Screening SOP (AML-SOP-001)
**Priority:** 🔴 CRITICAL  
**Why:** Regulatory blocker—FCA/NCA requirement, prevents financial crime, SAR procedures  
**Content:** Screening lists, match assessment, hit handling, ongoing monitoring  
**Pages:** 4–5  

### 5. Standing Order Processing SOP (STORD-SOP-001)
**Priority:** 🔴 CRITICAL  
**Why:** Core product feature, high-volume process, affects customer transactions  
**Content:** Creation, validation, execution, modification, cancellation  
**Pages:** 3  

### 6. Direct Debit Management SOP (DDEBIT-SOP-001)
**Priority:** 🔴 CRITICAL  
**Why:** Core product feature, high-volume process, regulatory (Direct Debit Guarantee)  
**Content:** Mandate collection, processing, failure handling, dispute procedures  
**Pages:** 3  

### 7. Exception Handling Guide (EXCP-SOP-001)
**Priority:** 🔴 CRITICAL  
**Why:** Operational quality—failed transactions must be resolved, customer impact significant  
**Content:** Exception types, investigation, resolution, escalation matrix  
**Pages:** 4  

### 8. Account Dormancy SOP (DORM-SOP-001)
**Priority:** 🟡 IMPORTANT  
**Why:** Regulatory requirement (unclaimed money), but lower volume (12-month trigger)  
**Content:** Dormancy identification, customer notification, reactivation  
**Pages:** 3  

### 9. Dormancy & Unclaimed Money SOP (UNCLAIMED-SOP-001)
**Priority:** 🟡 IMPORTANT  
**Why:** Regulatory requirement (long-term dormancy), but infrequent (~6 years)  
**Content:** Unclaimed money procedures, customer claim process  
**Pages:** 3–4  

### 10. Transaction Reversal SOP (TXN-REV-SOP-001)
**Priority:** 🟡 IMPORTANT  
**Why:** Customer service, but lower volume (mainly fraud/disputes)  
**Content:** Reversal request process, investigation, refund with interest  
**Pages:** 3  

---

# BATCH 2: PRODUCT DOCUMENTATION (10 Documents)

## Overview
4 CRITICAL | 6 IMPORTANT (includes T&Cs and legal docs)

### 1. Product Pricing Guide (PRC-GUIDE-2026)
**Priority:** 🔴 CRITICAL  
**Why:** Sales staff, customers, marketing all need current pricing  
**Content:** All products' interest rates, fees, charges (updated quarterly)  
**Pages:** 2–3  

### 2. SmartSaver Terms & Conditions (SAV-001-TC)
**Priority:** 🔴 CRITICAL  
**Why:** Legal requirement, customer protection, regulatory (FCA COBS)  
**Content:** Account terms, interest calculation, fees, dispute resolution  
**Pages:** 8–10  

### 3. Current Account Terms & Conditions (CURR-001-TC)
**Priority:** 🔴 CRITICAL  
**Why:** Legal requirement, customer protection, regulatory (FCA COBS)  
**Content:** Account terms, overdraft, payment protection, chargebacks  
**Pages:** 6–8  

### 4. Mortgage Terms & Conditions (MORT-001-TC)
**Priority:** 🔴 CRITICAL  
**Why:** Legal requirement, customer protection, major financial commitment  
**Content:** Loan terms, interest, prepayment, default, lender rights  
**Pages:** 10–12  

### 5. SmartSaver Product Specification (SAV-001-SPEC)
**Priority:** 🟡 IMPORTANT  
**Why:** Detailed spec for developers/operations, but Sales use T&Cs + Pricing Guide  
**Content:** Feature list, interest structure, opening requirements  
**Pages:** 4–5  

### 6. Current Account Product Specification (CURR-001-SPEC)
**Priority:** 🟡 IMPORTANT  
**Why:** Developer/operations reference, less critical than T&Cs  
**Content:** Features, overdraft terms, regulatory classification  
**Pages:** 3–4  

### 7. Mortgage Product Specification (MORT-001-SPEC)
**Priority:** 🟡 IMPORTANT  
**Why:** Developer/operations reference, less critical than T&Cs  
**Content:** Loan terms, underwriting requirements, collateral  
**Pages:** 5–6  

### 8. Interest Rate Schedule (IRS-2026-001)
**Priority:** 🟡 IMPORTANT  
**Why:** Updated monthly, used for rate lookups, but Pricing Guide covers main info  
**Content:** Current rates by product/tier, variation basis, review schedule  
**Pages:** 1–2  

### 9. Product FAQ (PROD-FAQ-001)
**Priority:** 🟡 IMPORTANT  
**Why:** Customer-facing, nice for support, but lower priority than T&Cs  
**Content:** Common questions per product with plain-language answers  
**Pages:** 4–5  

### 10. Product Information Documents Suite (PROD-PID-SUITE)
**Priority:** 🟡 IMPORTANT  
**Why:** FCA-mandated (COBS Part 6D), but standardized template (can auto-generate)  
**Content:** 5 PIDs (one per product) with pre-contract disclosures  
**Pages:** 2 pages each (10 total)  

---

# BATCH 3: TECHNOLOGY & ARCHITECTURE (8 Documents)

## Overview
4 CRITICAL | 4 IMPORTANT

### 1. Application Landscape (APP-LAND-001)
**Priority:** 🔴 CRITICAL  
**Why:** Architecture overview needed for anyone working with systems (developers, ops, architects)  
**Content:** System catalog (AMS, IBP, MBA, FDE, PPS, DW, etc.), ownership, SLAs  
**Pages:** 4–5  

### 2. System Context Diagram (CTX-DIAG-001)
**Priority:** 🔴 CRITICAL  
**Why:** Visual architecture—helps everyone understand system flow  
**Content:** Diagram (system boundaries, external systems, interfaces), legend  
**Pages:** 2–3  

### 3. API Catalogue (API-CAT-001)
**Priority:** 🔴 CRITICAL  
**Why:** Developers can't build without it—45+ endpoints documented  
**Content:** All APIs (accounts, payments, customers, cards, fraud), formats, auth, examples  
**Pages:** 8–10  

### 4. Production Runbook (PROD-RUNBOOK-001)
**Priority:** 🔴 CRITICAL  
**Why:** Operations need this for daily management—system startup, health checks, troubleshooting  
**Content:** Startup procedures, health checks, common issues, emergency contacts  
**Pages:** 6–7  

### 5. Logical Data Model (LDM-001)
**Priority:** 🟡 IMPORTANT  
**Why:** Data engineers and architects need it, but not operations-critical  
**Content:** ERD, entities, relationships, data dictionary, indexes  
**Pages:** 6–8  

### 6. Integration Specification (INT-SPEC-001)
**Priority:** 🟡 IMPORTANT  
**Why:** Integration designers/developers need it, but not operations-critical  
**Content:** Inter-system integrations, message formats, protocols, error handling  
**Pages:** 5–6  

### 7. Known Issues Register (KNOWN-ISSUES-001)
**Priority:** 🟡 IMPORTANT  
**Why:** Living document, nice to have, but lower priority  
**Content:** Current issues, severity, workarounds, status (updated continuously)  
**Pages:** 2–4  

### 8. Release Notes Archive (RELEASE-NOTES-2026)
**Priority:** 🟡 IMPORTANT  
**Why:** Documentation/historical reference, but not operations-critical  
**Content:** Release history (Q1–Q4 2026), features, fixes, known issues  
**Pages:** 2–3 per release (8–12 total)  

---

---

# 🎯 CRITICAL 15-DOC SUBSET (Recommended for Phase 3A)

## SOPs (7 documents)
1. ✅ Account Opening SOP (ACCT-OPEN-SOP-001) — **CREATED**
2. 🔴 Account Closure SOP (ACCT-CLOSE-SOP-001)
3. 🔴 KYC Verification SOP (KYC-SOP-001)
4. 🔴 AML Screening SOP (AML-SOP-001)
5. 🔴 Standing Order Processing SOP (STORD-SOP-001)
6. 🔴 Direct Debit Management SOP (DDEBIT-SOP-001)
7. 🔴 Exception Handling Guide (EXCP-SOP-001)

## Product Documentation (4 documents)
8. 🔴 Product Pricing Guide (PRC-GUIDE-2026)
9. 🔴 SmartSaver Terms & Conditions (SAV-001-TC)
10. 🔴 Current Account Terms & Conditions (CURR-001-TC)
11. 🔴 Mortgage Terms & Conditions (MORT-001-TC)

## Technology & Architecture (4 documents)
12. 🔴 Application Landscape (APP-LAND-001)
13. 🔴 System Context Diagram (CTX-DIAG-001)
14. 🔴 API Catalogue (API-CAT-001)
15. 🔴 Production Runbook (PROD-RUNBOOK-001)

---

---

# 📊 SUMMARY TABLE

| Category | Total | Critical (15-Doc Plan) | Important (Full 27) | Created |
|----------|-------|----------------------|-------------------|---------|
| **SOPs** | 10 | 7 | 3 | 1 ✅ |
| **Product Docs** | 10 | 4 | 6 | 0 |
| **Tech Docs** | 8 | 4 | 4 | 0 |
| **TOTAL** | **28** | **15** | **13** | **1** |

---

---

# 💾 COMPLETE CHECKLIST

## All 27 Remaining Documents to Create

### ✅ SOPs (7 Critical + 3 Important = 10 total)
- [ ] Account Closure SOP (ACCT-CLOSE-SOP-001) — 🔴 CRITICAL
- [ ] KYC Verification SOP (KYC-SOP-001) — 🔴 CRITICAL
- [ ] AML Screening SOP (AML-SOP-001) — 🔴 CRITICAL
- [ ] Standing Order Processing SOP (STORD-SOP-001) — 🔴 CRITICAL
- [ ] Direct Debit Management SOP (DDEBIT-SOP-001) — 🔴 CRITICAL
- [ ] Exception Handling Guide (EXCP-SOP-001) — 🔴 CRITICAL
- [ ] Account Dormancy SOP (DORM-SOP-001) — 🟡 IMPORTANT
- [ ] Dormancy & Unclaimed Money SOP (UNCLAIMED-SOP-001) — 🟡 IMPORTANT
- [ ] Transaction Reversal SOP (TXN-REV-SOP-001) — 🟡 IMPORTANT

### ✅ Product Docs (4 Critical + 6 Important = 10 total)
- [ ] Product Pricing Guide (PRC-GUIDE-2026) — 🔴 CRITICAL
- [ ] SmartSaver Terms & Conditions (SAV-001-TC) — 🔴 CRITICAL
- [ ] Current Account Terms & Conditions (CURR-001-TC) — 🔴 CRITICAL
- [ ] Mortgage Terms & Conditions (MORT-001-TC) — 🔴 CRITICAL
- [ ] SmartSaver Product Specification (SAV-001-SPEC) — 🟡 IMPORTANT
- [ ] Current Account Product Specification (CURR-001-SPEC) — 🟡 IMPORTANT
- [ ] Mortgage Product Specification (MORT-001-SPEC) — 🟡 IMPORTANT
- [ ] Interest Rate Schedule (IRS-2026-001) — 🟡 IMPORTANT
- [ ] Product FAQ (PROD-FAQ-001) — 🟡 IMPORTANT
- [ ] Product Information Documents (PROD-PID-SUITE) — 🟡 IMPORTANT

### ✅ Tech Docs (4 Critical + 4 Important = 8 total)
- [ ] Application Landscape (APP-LAND-001) — 🔴 CRITICAL
- [ ] System Context Diagram (CTX-DIAG-001) — 🔴 CRITICAL
- [ ] API Catalogue (API-CAT-001) — 🔴 CRITICAL
- [ ] Production Runbook (PROD-RUNBOOK-001) — 🔴 CRITICAL
- [ ] Logical Data Model (LDM-001) — 🟡 IMPORTANT
- [ ] Integration Specification (INT-SPEC-001) — 🟡 IMPORTANT
- [ ] Known Issues Register (KNOWN-ISSUES-001) — 🟡 IMPORTANT
- [ ] Release Notes Archive (RELEASE-NOTES-2026) — 🟡 IMPORTANT

---

## Recommended Execution

**Phase 3A (Critical 15):** 20–25 hours
**Phase 3B (Important 12 + Phase 4):** 15–20 hours

**Would you like to proceed with:**
- ✅ **Phase 3A only** (15 critical docs)
- ✅ **Phase 3A + B** (all 27 Phase 3 docs)
- ✅ **Phase 3A + Phase 4** (critical ops + customer experience)

---
