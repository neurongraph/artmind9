# FirstUK Bank — Anti-Money Laundering Policy

## Metadata

| Field | Value |
|-------|-------|
| Document ID | AML-POL-002 |
| Version | 2.2 |
| Effective Date | 2026-01-15 |
| Review Date | 2027-01-15 |
| Owner | Head of Financial Crime |
| Department | Financial Crime |
| Status | Active |
| Classification | Internal |
| Audience | All Staff, Compliance, Risk, Retail Banking |
| Regulatory Reference | Money Laundering Regulations 2017, POCA 2002, Terrorism Act 2000, FCA COBS Part 10 |
| Related Documents | [[business_ontology]], [[policy_customer_identification]], [[departments]] |

---

## Executive Summary

FirstUK Bank maintains a comprehensive Anti-Money Laundering (AML) and Counter-Financing of Terrorism (CFT) policy to prevent financial crime, protect the bank's reputation and license, and meet regulatory obligations. This policy establishes mandatory procedures for AML screening, customer due diligence, transaction monitoring, and suspicious activity reporting.

---

## Purpose & Scope

### Purpose

To establish procedures for AML/CFT compliance that:
- Detect and prevent money laundering and terrorist financing
- Comply with Money Laundering Regulations 2017, POCA, and Terrorism Act
- Implement Proceeds of Crime Act obligations
- File Suspicious Activity Reports (SARs) with National Crime Agency (NCA)
- Maintain sanctions list compliance (OFAC, EU, HMT, UN)

### Scope

Applies to:
- All customers and accounts
- All transactions (deposits, withdrawals, transfers)
- All staff, contractors, and third parties
- All products and channels (branch, online, mobile)

Does not apply to:
- Regulatory authorities
- Law enforcement
- Exempt entities (per HM Treasury guidance)

---

## Policy Statement

**FirstUK Bank does not knowingly provide banking services to individuals or entities engaged in or suspected of money laundering, terrorist financing, sanctions violations, or other financial crime.**

### Core Requirements

1. **Customer Screening:** Mandatory AML screening at account opening
2. **Ongoing Monitoring:** Continuous transaction pattern analysis
3. **Suspicious Activity Reporting:** Report suspected financial crime to NCA
4. **Record Retention:** 5 years minimum for all AML records
5. **Staff Awareness:** Annual AML training mandatory
6. **Escalation:** Confidential reporting procedures

---

## AML Screening Framework

### Screening Lists & Sources

**Mandatory Screening Databases:**

| List | Authority | Frequency | Purpose |
|------|-----------|-----------|---------|
| OFAC Sanctions List | US Treasury | Daily | Sanction compliance |
| EU Consolidated List | European Commission | Daily | EU sanctions |
| HMT Sanctions List | UK Treasury | Daily | UK sanctions |
| UN Sanctions List | UN Security Council | Weekly | UN terrorism sanctions |
| PEP Database | Third-party provider | Real-time | Politically Exposed Persons |
| Adverse Media | Third-party provider | Real-time | Criminal/fraud records |

### Screening Process

**Step 1: Initial Screening (Account Opening)**
- Customer name screened against all mandatory lists
- Immediate family screened (if available)
- Close associates screened (if identified)
- Results documented in system

**Step 2: Match Review**
- Potential matches escalated to Financial Crime team
- False positive assessment (name similarity vs. true match)
- If true positive: Account blocked, escalation to Compliance Head

**Step 3: Hit Escalation**
- **Sanctions Hit:** Immediate account freeze + NCA notification
- **PEP Flag:** Enhanced due diligence mandatory
- **Adverse Media:** Risk assessment + possible rejection

**Step 4: Ongoing Monitoring**
- Periodic re-screening (high-risk customers: quarterly; standard: annually)
- New sanctions lists checked regularly
- Transaction monitoring (see below)

---

## Customer Due Diligence (CDD)

See [[policy_customer_identification]] for Identity and Address Verification.

**AML-Specific CDD Elements:**

### Source of Funds Verification

**Required for:**
- Initial deposit >£10,000
- Large transactions (>£50,000)
- High-risk customers
- Business account funding

**Acceptable Evidence:**
- Employment letter + payslips
- Business accounts/tax returns
- Property sale documentation
- Inheritance documentation
- Gift confirmation letter

**Process:**
1. Request source of funds information
2. Verify documentation
3. Record in system
4. Flag if source unclear or suspicious

### Business Purpose

**Required for:**
- Business customers
- Accounts with unusual transaction patterns
- High-value accounts

**Information Collected:**
- Business type and nature
- Expected transaction volumes
- Expected geographic markets
- Beneficial ownership (if not natural person)

---

## Enhanced Due Diligence (EDD)

**Mandatory for High-Risk Customers:**

**High-Risk Indicators:**
- Beneficial ownership unclear
- Jurisdictions with weak AML controls
- PEP or family member
- Sanctions list match
- Adverse media
- High-value accounts
- Complex transaction patterns
- Non-face-to-face relationship

**EDD Procedures:**
1. Senior staff approval required
2. Additional identity verification
3. Beneficial ownership investigation (if entity)
4. Source of funds verification
5. Enhanced ongoing monitoring
6. Quarterly risk reassessment
7. Documentation of risk assessment

**Approval Authority:** Compliance Head

---

## Transaction Monitoring

### Purpose

Detect suspicious transaction patterns indicative of money laundering or terrorist financing.

### Monitoring Methods

**Automated Rules (Fraud Detection Engine, see [[systems]]):**

| Rule | Threshold | Action |
|------|-----------|--------|
| Large deposit | >£50,000 in 24h | Alert + manual review |
| Rapid movement | Deposit then withdrawal <2h | Alert for review |
| Multiple small transfers | 10+ transfers to different recipients <24h | Velocity alert |
| Geographic anomaly | Transaction in different country within 2 hours | Alert for review |
| High-risk jurisdiction | Transaction with sanctioned country | Auto-block |
| Unusual beneficiary | Transfer to new beneficiary >threshold | Alert for review |

**Manual Monitoring:**
- Financial Crime team reviews alerts
- Pattern analysis for suspicious sequences
- Behavioral profiling changes
- Judgment-based assessment

### Transaction Profile Assessment

**Customer Risk Profile = Transaction Pattern Analysis**

**Normal Profile Examples:**
- Salary deposits monthly + regular bills
- Periodic savings deposits + interest credits
- Business deposits + operational expenses
- Pension payments + utility bills

**Suspicious Pattern Examples:**
- Large deposit immediately followed by international transfer
- Round-dollar amounts to multiple countries
- Cash deposits followed by wire transfers out
- Deposits using different names/accounts
- Structuring (repeated transfers just below reporting threshold)

### Action on Suspicious Transaction

**Step 1: Initial Alert**
- System generates alert or staff observation
- Transaction placed on hold (up to 5 business days)
- Financial Crime team reviews immediately

**Step 2: Investigation**
- Customer contacted for explanation (if safe to do so)
- Supporting documentation reviewed
- Transaction history analyzed
- Risk assessment updated

**Step 3: Decision**
- **Proceed:** Suspicious activity reported, transaction approved
- **Reject:** Decline transaction (without informing customer of AML reason)
- **Block:** Account frozen pending investigation
- **SAR File:** Suspicious Activity Report filed with NCA

**Step 4: Documentation**
- All decisions documented with reasoning
- File marked confidential
- No customer disclosure (tipping off prohibited)

---

## Suspicious Activity Reporting (SAR)

### When to File a SAR

**Mandatory SAR Filing Triggers:**
- Money laundering suspected (any amount)
- Terrorist financing suspected
- Proceeds of crime (POCA) suspected
- Sanctions violation
- Large cash transactions with no obvious purpose
- Customers unwilling to provide information
- Structuring detected (deliberately avoiding reporting thresholds)

**Discretionary SAR:**
- Unusual transaction patterns not clearly illegal
- Potential fraud (separate from AML fraud)
- Regulatory concern

### SAR Filing Process

**Step 1: Report Preparation**
- Detailed narrative of suspicious activity
- Timeline of transactions
- Customer and transaction details
- Supporting evidence
- Risk assessment
- Recommendation (ML suspected, TF suspected, or other)

**Step 2: Internal Approval**
- Compliance Head review and approval
- Chief Risk Officer sign-off (if significant)
- CEO notification (if high-profile)

**Step 3: NCA Submission**
- Filed via Suspicious Activity Report Online (SARs Online)
- Filed within 30 days of suspicion arising
- Submission confirmed by NCA

**Step 4: Follow-Up**
- NCA may request additional information
- Bank cooperates fully with any investigation
- Document retention (5 years minimum)

### SAR Confidentiality (Tipping Off)

**Critical:** Do not inform customer of SAR filing.

**Tipping Off Prohibition:**
- Cannot tell customer a SAR has been filed
- Cannot disclose grounds for suspicion
- Cannot describe specific suspicious transactions
- Cannot delay transaction to prepare SAR and then inform customer

**Exception:** SAR already filed and transaction proceeding normally (communication not tipping off)

**Breach Consequences:**
- Criminal offense (up to 2 years imprisonment)
- Regulatory fine
- Disciplinary action (termination)

---

## Sanctions Compliance

### Sanctions Programs Monitored

- **OFAC:** US Office of Foreign Assets Control
- **EU:** European Union Consolidated Sanctions List
- **HMT:** UK Treasury Sanctions Designations List
- **UN:** UN Security Council sanctions (terrorism, etc.)

### Sanctions Screening

**Screening Requirement:**
- Customer name checked against all lists
- Transaction screening for high-risk jurisdictions
- Real-time updates (lists change daily)
- Supplementary screening (immediate family, associates)

**Hit Processing:**
- Potential match = immediate account freeze
- Manual review of match (false positive assessment)
- If true hit = escalate to Compliance Head + immediately file NCA notice
- Account remains blocked pending resolution

**Blocked Account Procedures:**
- No transactions permitted
- Asset freeze (prevent removal of funds)
- Customer not informed of reason (confidential)
- NCA notification (Office of Financial Sanctions Implementation, OFSI)
- Potential criminal liability

---

## Beneficial Ownership (for Non-Individual Customers)

**Scope:** Applies to trusts, partnerships, corporations, investment vehicles

**Identification Requirement:**
- Identify ultimate beneficial owners
- Obtain ownership percentages
- Verify identity of all beneficial owners
- Document source of control/ownership

**Enhanced Identification:**
- >25% ownership requires verification
- Consider chains of control
- Investigate shell company structures
- Assess likelihood of beneficial ownership abuse

**High-Risk Scenarios:**
- Opaque ownership structure
- Bearer shares or trusts
- Multiple layers of intermediaries
- Beneficial owners in high-risk jurisdictions

---

## Correspondent Banking (Future)

**Policy Note:** Currently FirstUK Bank does not engage in correspondent banking or money transfer services. If future expansion planned, enhanced due diligence required per Correspondent Banking AML standards.

---

## Staff Responsibilities

### All Staff

- Annual AML training completion
- Report suspicious activity to manager/compliance
- Follow tipping off rules strictly
- Maintain confidentiality
- Know customer procedures (KYC)

### Branch Staff & Customer Service

- Conduct/support customer verification
- Report unusual transactions
- Escalate customer inquiries about frozen accounts (no detail provided)
- Maintain vigilance for suspicious patterns

### Compliance & Financial Crime

- Execute AML screening procedures
- Investigate alerts
- File SARs
- Maintain AML audit trail
- Support regulatory examinations

### Compliance Head

- Approve SAR filings
- Manage EDD approvals
- Oversee training
- Report to Board Risk Committee
- External regulatory liaison

---

## Training & Awareness

**Mandatory Annual Training:**
- All staff: 1-hour AML training (certification required)
- Customer-facing staff: Additional 1-hour practical scenarios
- Compliance staff: Quarterly updates + specialist certifications

**Training Topics:**
- Money laundering methods and trends
- AML/CFT regulatory framework
- Red flags and suspicious indicators
- Company procedures and systems
- Tipping off and confidentiality
- SAR filing process
- Sanctions screening
- Customer scenarios and case studies

**Non-Compliance:** Failure to complete training = disciplinary action

---

## Audit & Monitoring

**Internal Audit:**
- Annual testing of AML procedures
- Sample file review (KYC, EDD, SAR decisions)
- Control testing
- AML system validation

**Regulatory Audit:**
- FCA/PRA periodic AML examinations
- Thematic reviews
- Consent orders (if violations found)

**Performance Metrics:**
| Metric | Target |
|--------|--------|
| AML screening completion rate | 100% |
| SAR filing accuracy | 100% |
| SAR filing timeliness (within 30 days) | 95%+ |
| High-risk customer EDD completion | 100% |
| Staff training completion | 100% annual |

---

## Regulatory Breaches & Escalation

**Consequences of AML Violations:**

- **Regulatory Fine:** FCA/PRA fines (millions of pounds)
- **Criminal Liability:** Directors/staff: up to 14 years imprisonment
- **Reputational:** License revocation, business closure
- **Customer Impact:** Frozen funds, account closure

**Internal Escalation:**
- SAR filing = Compliance Head approval
- Large SAR = CRO notification
- Significant breach = CEO and Board notification
- Potential regulatory self-disclosure

---

## Record Retention

**AML Records Retained:**
- Customer verification documents (5 years post-closure minimum)
- SAR files (5 years)
- Transaction records related to SAR (5 years)
- Risk assessments (5 years)
- Screening results (5 years)

**Storage:** Secure document management system (DMS)  
**Access Control:** Compliance staff only  
**Audit Trail:** All access logged  

---

## Policy Review & Updates

**Review Frequency:** Annual (or upon regulatory change)  
**Last Review:** 2026-01-15  
**Next Review:** 2027-01-15  

**Regulatory Change Triggers:**
- FinCEN or FATF guidance updates
- FCA/PRA rule changes
- Money Laundering Regulations amendments
- Sanctions program updates

---

## Related Documents

- [[policy_customer_identification]] — KYC Policy
- [[departments]] — Financial Crime Department
- [[business_ontology]] — AML Screening entity
- AML Screening SOP (AML-SOP-001)
- Account Opening SOP (ACCT-OPEN-SOP-001)
- NCA Suspicious Activity Report guidance
- HM Treasury Sanctions guidance

---

## Document Version History

| Version | Date | Change | Author |
|---------|------|--------|--------|
| 2.2 | 2026-01-15 | Added sanctions screening detail, beneficial ownership section | Head of Financial Crime |
| 2.1 | 2025-07-01 | Updated screening lists and procedures | Head of Financial Crime |
| 2.0 | 2025-01-01 | Annual review, tipping off emphasis | Head of Financial Crime |
| 1.0 | 2024-01-01 | Initial policy | Head of Financial Crime |

---

## Sign-Off

**Approved by:**  
Head of Financial Crime — **Date: 2026-01-15**  
Head of Compliance — **Date: 2026-01-15**  
Chief Risk Officer — **Date: 2026-01-15**  
Chief Executive Officer — **Date: 2026-01-15**

---
