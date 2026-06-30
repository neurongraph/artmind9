# FirstUK Bank — AML Screening SOP

## Metadata

| Field | Value |
|-------|-------|
| Document ID | AML-SOP-001 |
| Version | 1.1 |
| Effective Date | 2026-01-15 |
| Review Date | 2027-01-15 |
| Owner | Head of Financial Crime |
| Department | Financial Crime |
| Status | Active |
| Classification | Internal |
| Audience | Financial Crime Staff, Compliance, Branch Staff |
| Related Documents | [[policy_aml]], [[policy_customer_identification]], [[business_ontology]] |

---

## Purpose

Establish procedures for AML screening to detect and prevent money laundering, terrorist financing, and sanctions violations through systematic screening of customers against regulatory lists.

---

## Scope

Applies to:
- All new customer account openings (mandatory)
- Periodic re-screening (high-risk customers quarterly, standard annually)
- Transaction monitoring (ongoing, automated)
- Suspicious activity assessment and reporting

---

## Process Overview

```
Customer Application Received
    ↓
Step 1: Initiate Screening
    ↓
Step 2: Screen Against Lists
    ↓
Step 3: Assess Match (if found)
    ↓
    ├─ No Match: Proceed ✅
    └─ Match Found: Manual Review 🔍
    ↓
Step 4: Record Results
    ↓
Step 5: Hit Handling (if applicable)
    ↓
✅ AML Screening Complete
```

**Timeline:** <5 minutes (automated), 1–24 hours (manual review if hit)

---

## Step 1: Initiate Screening

### Screening Trigger

**Automatic Screening On:**
- New customer account opening (before account creation)
- Periodic re-screening (per risk-based schedule)
- Manual request (compliance concern, unusual activity)

**Information Required for Screening:**
- Customer full legal name
- Date of birth (if available)
- Nationality
- Country of residence
- Business name (if applicable)

**Entry into Screening System:**
- Compliance staff enters customer information
- System prepares screening query
- Initiates screening against all configured lists

---

## Step 2: Screen Against Regulatory Lists

### Screening Lists (All Mandatory)

**1. OFAC Sanctions List**
- **Authority:** US Treasury Office of Foreign Assets Control
- **List:** Specially Designated Nationals and Blocked Persons (SDN)
- **Coverage:** Global individuals and entities under US sanctions
- **Update Frequency:** Daily
- **Hits:** Any match = immediate escalation

**2. EU Consolidated Sanctions List**
- **Authority:** European Commission
- **List:** EU Common Foreign & Security Policy sanctions
- **Coverage:** Individuals and entities under EU sanctions
- **Update Frequency:** Daily
- **Hits:** Any match = immediate escalation

**3. HM Treasury Sanctions List**
- **Authority:** UK Treasury (Office of Financial Sanctions Implementation, OFSI)
- **List:** UK sanctions designations
- **Coverage:** Individuals and entities under UK sanctions
- **Update Frequency:** Daily
- **Hits:** Any match = immediate escalation

**4. UN Sanctions List**
- **Authority:** UN Security Council
- **List:** Consolidated UN sanctions (terrorism, weapons, etc.)
- **Coverage:** Global terrorism and other UN sanctions
- **Update Frequency:** Weekly
- **Hits:** Any match = immediate escalation

**5. PEP Database**
- **Authority:** Third-party data provider (World-Check, Dow Jones, etc.)
- **List:** Politically Exposed Persons (government officials, senior politicians, military)
- **Coverage:** Current and former government officials, their families, close associates
- **Update Frequency:** Real-time
- **Hits:** PEP match = enhanced due diligence (not auto-block)

**6. Adverse Media Screening**
- **Authority:** News database aggregator
- **List:** Criminal convictions, fraud allegations, sanctions violations (from news)
- **Coverage:** Negative news mentions
- **Update Frequency:** Daily
- **Hits:** Material adverse media = escalation for assessment

### Screening Execution

**Automated Process:**
1. System enters customer name into screening engine
2. Screening engine searches all configured lists
3. System checks for exact and close-match results
4. Results returned (typically <1 minute)

**Screening Parameters:**
- Match threshold: Configurable (typically 80%+ match)
- Close matches included (name variations, typos)
- Field matching: Name, DOB, nationality
- Result includes: Matched name, list, match score

---

## Step 3: Assess Screening Results

### No Match Scenario

**Results:**
- ✅ Screening shows NO hits across all lists
- ✅ Customer not on any sanctions/watchlist
- ✅ No adverse media identified

**Action:**
- Proceed with account opening (KYC already complete)
- Record "screening passed" in system
- Note screening date and system reference
- Continue to AML compliance checklist

**Timeline:** Immediate

### Match Found Scenario

**Results:**
- 🔍 Screening shows potential HIT (match found)
- Match score displayed (% likelihood match)
- Matched name and list identified

**Action:**
- STOP account opening immediately
- Escalate to Financial Crime team for manual review
- Document match details in system
- Initiate manual review process

---

## Step 4: Manual Review Process (Hit Assessment)

### Step 4A: Preliminary Assessment

**Financial Crime Staff Reviews:**
1. **Match Score:** How close is the match? (80%+ = likely match)
2. **Name Variance:** Typo/nickname vs. exact match?
3. **Date of Birth:** Does DOB match or differ significantly?
4. **Nationality:** Same or different country?
5. **Business:** Is match against individual or organization?

**Initial Decision Options:**
- **A) Clear False Positive:** Similar name but different person → Clear to proceed
- **B) Likely True Positive:** Name, DOB, nationality all match → Escalate immediately
- **C) Uncertain:** Partial match requiring investigation → Deeper research

### Step 4B: True Positive Investigation

**If True Positive Confirmed:**
1. **Identify Reason:** Which list? What's the designation (sanctions, PEP, adverse media)?
2. **Determine Action:**
   - **Sanctions Hit:** Automatic FREEZE + Escalate to OFSI
   - **PEP Hit:** Enhanced due diligence required (escalate to Compliance Head)
   - **Adverse Media:** Risk assessment (may proceed with monitoring)
3. **Document Investigation:**
   - Notes on match assessment
   - Reason for decision
   - Evidence reviewed
   - Approver name/date

### Step 4C: False Positive Resolution

**If False Positive Determined:**
- Document reasoning (different person due to...)
- Record in system (prevents re-flagging)
- Proceed with account opening
- Archive false positive assessment

**Timeline for Resolution:** 2–4 hours

---

## Step 5: Record Results

### System Documentation

**Record Fields:**
- Screening reference number (system-generated)
- Customer ID
- Screening date/time
- Lists searched
- Results: Pass / Hit (specify which)
- If hit: Match score, matched name, list
- If reviewed: Decision, approver, resolution date
- Status: Cleared / Blocked / Under Review

**Storage:**
- AMS (Account Management System)
- Linked to customer record
- Accessible for audit
- Retained for 5 years minimum (AML regulations)

---

## Step 6: Hit Handling

### Type 1: Sanctions Hit (OFAC/EU/HMT/UN)

**Action: IMMEDIATE BLOCK**

**Steps:**
1. Account FROZEN immediately (no transactions permitted)
2. Escalate to Compliance Head + Head of Financial Crime
3. Notify OFSI (UK Treasury) within 24 hours (if UK sanctions hit)
4. File Suspicious Activity Report (SAR) with NCA
5. Document hold reason in system
6. Do NOT inform customer of reason (tipping off risk)

**Customer Communication (If Account Opened):**
- Generic message: "Your account has been frozen pending review"
- No mention of AML/sanctions (confidential)
- Escalate to manager if customer questions

**Timeline:** Immediate + regulatory notification within 24 hours

**Outcome Options:**
- ✅ Hit confirmed as false positive → Unfreeze, apologize, proceed
- ❌ Hit confirmed as true positive → Remain frozen, potential account closure

### Type 2: PEP Hit

**Action: ENHANCED DUE DILIGENCE (Not automatic block)**

**Steps:**
1. Flag customer as "PEP"
2. Escalate to Compliance Head for review
3. Assess risk (family member? Close associate? Foreign PEP?)
4. Approval decision: Proceed with EDD / Reject application
5. If proceed: Enhanced monitoring requirements
6. Document risk assessment

**Enhanced Due Diligence Requirements:**
- Beneficial ownership verification (if applicable)
- Source of funds documentation
- Ongoing transaction monitoring (quarterly)
- Annual review

**Timeline:** 1–2 business days

### Type 3: Adverse Media Hit

**Action: RISK ASSESSMENT**

**Steps:**
1. Review news/adverse media details
2. Assess materiality (relevance to banking relationship)
3. If material: Escalate to Compliance Head
4. Decision: Proceed with monitoring / Reject application
5. Document assessment

**Example Assessment:**
- ✅ PASS: Old fraud accusation (10+ years ago, no conviction) → Proceed with standard monitoring
- ❌ REJECT: Recent sanctions violation (confirmed) → Reject application

**Timeline:** 1–2 business days

---

## Step 7: Ongoing Monitoring

### Post-Screening Monitoring

**High-Risk Customers:**
- Quarterly re-screening (re-scan sanctions lists)
- Enhanced transaction monitoring
- Unusual activity alerts reviewed manually
- Annual comprehensive risk review

**Standard-Risk Customers:**
- Annual re-screening (re-scan sanctions lists)
- Automated transaction monitoring
- Alerts reviewed by automated rules

**Monitoring Triggers:**
- Large transaction (>£50,000)
- Transaction to high-risk jurisdiction
- New beneficiary or payee
- Rapid transaction velocity (multiple in short time)

---

## Troubleshooting

### Screening System Down

**If Screening System Unavailable:**
1. Document outage time
2. Do NOT proceed with account opening (screening mandatory)
3. Escalate to Technology team (immediate resolution)
4. Retry once system restored
5. Account opening delayed until screening complete

### False Positive Frequently Triggered

**If Customer Frequently Hit on Screening:**
1. Document previous false positive assessments
2. Update customer profile with "verified" flag
3. Re-screen may still trigger (due to list updates), but quick resolution
4. Consider alternative name variant for faster screening

---

## Quality Assurance

### Screening Audit

**Monthly Audit:**
- Review 10+ screening decisions
- Verify hits properly escalated
- Confirm false positives documented
- Assess timeliness of decisions

**Annual Audit:**
- Full screening process review
- List coverage verification (all lists active)
- Hit resolution timeliness
- SAR filing appropriateness

---

## Related Documents

- [[policy_aml]] — AML Policy (detailed requirements)
- [[sop_kyc_verification]] — KYC verification (precedes AML screening)
- [[sop_account_opening]] — Account opening (AML screening is Step 5)
- [[business_ontology]] — Customer and AML Screening entities

---

## Document Version History

| Version | Date | Change | Author |
|---------|------|--------|--------|
| 1.1 | 2026-01-15 | Added hit handling procedures, ongoing monitoring | Head of Financial Crime |
| 1.0 | 2025-01-01 | Initial SOP | Head of Financial Crime |

---

## Sign-Off

**Approved by:**  
Head of Financial Crime — **Date: 2026-01-15**  
Head of Compliance — **Date: 2026-01-15**

---
