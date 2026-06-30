# FirstUK Bank — Customer Identification Policy

## Metadata

| Field | Value |
|-------|-------|
| Document ID | CID-POL-001 |
| Version | 2.1 |
| Effective Date | 2026-01-15 |
| Review Date | 2027-01-15 |
| Owner | Head of Compliance |
| Department | Compliance |
| Status | Active |
| Classification | Internal |
| Audience | All Staff, Branch Managers, Compliance, Risk |
| Regulatory Reference | FCA COBS Part 10, Money Laundering Regulations 2017 |
| Related Documents | [[business_ontology]], [[departments]], [[policy_aml]] |

---

## Executive Summary

FirstUK Bank maintains a comprehensive Customer Identification Policy (KYC) to ensure customer legitimacy, prevent financial crime, and comply with regulatory requirements. This policy establishes mandatory procedures for verifying customer identity and address at account opening and periodically thereafter.

---

## Purpose & Scope

### Purpose

To establish procedures for Know Your Customer (KYC) verification that:
- Confirm customer identity
- Verify customer address
- Assess customer risk profile
- Meet FCA COBS and Money Laundering Regulations requirements
- Prevent opening accounts for sanctioned individuals or entities

### Scope

Applies to:
- All customer account openings (retail, joint accounts)
- All new customer relationships
- Periodic re-verification (per [[business_ontology]] KYC Verification entity)
- All staff involved in customer onboarding

Does not apply to:
- Existing customers (unless triggered by risk reassessment)
- Regulatory authorities
- Internal bank accounts

---

## Policy Statement

**FirstUK Bank requires that all customers be positively identified and verified before account opening or product provision.**

### Core Requirements

1. **Identity Verification:** Mandatory at account opening
2. **Address Verification:** Mandatory at account opening
3. **Documentation:** Physical evidence required
4. **Record Retention:** 6 years post-account closure (see [[policy_retention]])
5. **Periodic Review:** Every 3–5 years for lower-risk customers; annually for high-risk customers
6. **Exception Process:** Escalation to Compliance Head for non-standard scenarios

---

## Identity Verification Procedures

### Acceptable Documents (Primary)

**UK Customers:**
- **UK Passport** — Valid, not expired
  - Document check: Signature, photo, personal details
  - Verification method: Direct examination or certified copy
  
- **UK Driving License** — Valid, not expired, photocard
  - Document check: Photo, signature, address, driving record
  - Verification method: DVLA verification (online check)
  
- **UK National ID Card** — Valid, not expired (when introduced)

**Non-UK Customers:**
- Foreign passport (English-speaking country preference)
- Driving license (EU equivalent)
- National ID card (EU equivalent)

### Identification Process

**Step 1: Document Collection**
- Request primary document from customer
- Collect document ID, issue date, expiry date
- Assess document integrity (no tampering, alterations)

**Step 2: Physical Verification**
- Examine original document in person (branch opening) or certified copy (postal)
- Verify photo matches customer appearance (if in-person)
- Check signature matches customer specimen
- Confirm document not expired

**Step 3: System Recording**
- Record document type, number, issue/expiry dates in AMS (Account Management System)
- Link to [[business_ontology]] Identification Document entity
- Flag any concerns for manual review

**Step 4: Verification Method by Channel**

**Branch Opening (Highest Assurance):**
- Physical examination of original document
- Staff verify photo against customer
- Immediate verification in-person
- Record: "In-branch verified" + staff name + date

**Online Opening (Medium Assurance):**
- Customer uploads certified copy (notarized)
- Automated document validation (if available)
- Manual review by Compliance staff
- Record: "Document-based verification" + reviewer name + date

**Postal Opening (Lower Assurance):**
- Customer sends certified copy by registered mail
- Manual review by Compliance
- Cross-reference with address verification
- Follow-up verification call recommended
- Record: "Postal verification" + reviewer name + date

---

## Address Verification Procedures

### Acceptable Address Documents

**Primary (Preferred):**
- **Utility Bill** — Gas, electricity, water, telephone
  - Requirement: Current bill (dated within last 3 months)
  - Check: Matches customer name and address
  - Acceptable providers: Major suppliers only
  
- **Council Tax Document** — Council tax bill or banding notice
  - Requirement: Current year
  - Check: Matches name and address
  
- **Bank Statement** — Another bank's statement
  - Requirement: Current (within 3 months)
  - Check: Shows name and address
  - Limitation: Not accepted as sole evidence if customer has account with us

**Secondary (If Primary Unavailable):**
- Mortgage statement
- Tenancy agreement (signed, with landlord details)
- Insurance policy (buildings/contents)
- HM Revenue & Customs correspondence
- Electoral register extract

### Address Verification Process

**Step 1: Document Collection**
- Request address verification document
- Record document type, date, address shown

**Step 2: Verification**
- Compare address on ID document with address on supporting document
- Confirm address is within UK (or approved jurisdiction)
- Check postcode format (UK postcode: A(1,2)9(1,2)A 9A(2))

**Step 3: System Recording**
- Record address document type and date in system
- Link to [[business_ontology]] Address entity
- Flag any discrepancies

---

## Risk-Based Customer Assessment

### Customer Risk Categories

**Low Risk:**
- Age 18–65
- UK national
- UK address
- Standard products (savings, current account)
- No adverse media
- Not PEP
- No high-value transactions

**Medium Risk:**
- Age >65 or <18 (>16 allowed for limited products)
- Non-UK resident but UK citizen
- Higher-value account (>£100k)
- Professional or business customer
- Minor PEP connections

**High Risk:**
- Non-UK citizen with foreign address
- Significant PEP exposure
- Beneficial owner unclear
- Transaction pattern suggests higher risk
- Country of residence on OFAC/sanctions list
- Adverse media identified
- Referred by Compliance

### Risk-Based Re-verification Schedule

| Customer Risk | Re-verification Frequency |
|---------------|--------------------------|
| Low | Every 5 years |
| Medium | Every 3 years |
| High | Annually or on-transaction basis |

---

## Enhanced Due Diligence (EDD)

Mandatory for high-risk customers and certain scenarios:

**Trigger Scenarios:**
- Customer from high-risk country
- PEP (Politically Exposed Person) identified
- Beneficial ownership unclear
- Transaction pattern unusual
- Complaint or fraud history
- Regulatory concern identified

**EDD Procedures:**
1. Enhanced identity verification (multiple documents)
2. Beneficial ownership verification (if customer is entity)
3. Source of funds verification
4. Ongoing transaction monitoring
5. Periodic review (annual minimum)
6. Documentation of risk assessment

**Escalation:** Compliance Head approval required for EDD customers

**See:** [[policy_aml]] for AML screening requirements

---

## PEP Screening

**Definition:** PEP = Politically Exposed Person (government official, senior politician, military leader, etc.)

**Screening Requirements:**
- Screen all new customers against PEP databases
- Screen immediate family of PEP
- Screen close associates of PEP
- Ongoing monitoring for PEP status changes

**PEP Database Sources:**
- FCA/PRA PEP lists
- Sanctions list cross-references
- Third-party data providers

**Action if PEP Identified:**
1. Flag customer in system as PEP
2. Enhanced due diligence mandatory
3. Escalate to Compliance Head
4. Enhanced ongoing monitoring
5. Consider transaction restrictions

---

## Adverse Media Screening

**Scope:** Screen all customers for adverse media (criminal convictions, fraud allegations, sanctions violations, etc.)

**Sources:**
- News databases
- Third-party screening providers
- Regulatory announcements

**Action if Adverse Media Found:**
1. Flag for manual review
2. Assess materiality (relevance to banking relationship)
3. If material: Escalate to Compliance Head
4. Consider account rejection or enhanced monitoring

---

## Documentation & Record Retention

### Required Records

For each customer:
- Copy of identity document
- Copy of address verification document
- KYC verification record (date, method, approver)
- Risk assessment (if high-risk)
- PEP screening result
- Adverse media screening result
- AML screening result

**Storage Location:** Secure document management system (DMS-001)  
**Access Control:** Role-based (Compliance staff only)  
**Audit Trail:** All access logged  
**Retention:** 6 years post-account closure (see [[policy_retention]])

---

## Exceptions & Escalation

**Non-Standard Scenarios Requiring Escalation:**

1. **Customer Unable to Provide Standard Documents**
   - Example: Refugee with limited documentation
   - Escalation: Compliance Head approval
   - Alternative verification: Notarized statement, passport alternative

2. **Foreign Customer with Limited UK Documentation**
   - Escalation: Compliance Head
   - Alternative: Foreign equivalent documents + additional address verification

3. **High-Value Customer with Complex Structure**
   - Example: Trust, investment vehicle
   - Escalation: Compliance Head + Legal review
   - Requirement: Beneficial ownership verification + enhanced due diligence

4. **Politically Exposed Person (PEP)**
   - Escalation: Compliance Head mandatory
   - Requirement: Enhanced due diligence, senior approval

**Escalation Process:**
1. Document reasoning for exception
2. Obtain written approval from Compliance Head
3. Record decision and rationale in system
4. Higher-risk monitoring applied

---

## Staff Responsibilities

### Branch Staff

- Collect required identity and address documents
- Conduct physical examination (branch opening)
- Record document details accurately
- Escalate exceptions to manager
- Maintain customer confidentiality

### Compliance Staff

- Review KYC documentation
- Approve or reject applications
- Conduct PEP/adverse media screening
- Manage escalations
- Maintain audit trail

### Compliance Head

- Policy approval and updates
- Exception approval (high-risk cases)
- Audit oversight
- Training and guidance
- External regulatory liaison

---

## Training & Awareness

**Mandatory Training:**
- All customer-facing staff: Annual KYC training
- Compliance staff: Quarterly updates + specialist training
- Managers: Annual policy overview + escalation procedures

**Training Topics:**
- Document recognition and authenticity
- Acceptable forms of identity/address verification
- Risk assessment
- Escalation procedures
- Regulatory requirements
- Customer privacy

---

## Non-Compliance

**Failure to verify customer identity is a regulatory breach with potential consequences:**

- Regulatory fine (FCA)
- Account closure (mandatory for non-compliant customers)
- Reputational damage
- Criminal liability (in severe cases)
- Inability to operate banking services

**Internal Consequences:**
- Disciplinary action (up to termination)
- Performance management review
- Loss of customer-facing role

---

## Audit & Monitoring

**Internal Audit:**
- Annual KYC procedure testing (see [[departments]] Internal Audit)
- Sample testing of completed KYC files
- Compliance with documentation standards
- Escalation handling appropriateness

**Regulatory Audit:**
- FCA/PRA periodic examinations
- Thematic reviews of KYC effectiveness
- File sampling and assessment

**Performance Metrics:**
| Metric | Target |
|--------|--------|
| KYC completion rate on opening | 100% |
| KYC rejection rate (suspicious) | <2% |
| Average KYC processing time | <1 business day |
| Exception escalation rate | <5% |
| Audit finding remediation | 100% within SLA |

---

## Policy Review & Updates

**Review Frequency:** Annual (or upon regulatory change)  
**Last Review:** 2026-01-15  
**Next Review:** 2027-01-15  

**Regulatory Change Triggers:**
- FCA COBS rule changes
- Money Laundering Regulations updates
- Court judgments affecting KYC
- Guidance from regulatory authorities

---

## Related Documents & References

- [[business_ontology]] — KYC Verification entity definition
- [[policy_aml]] — Anti-Money Laundering Policy
- [[policy_retention]] — Document Retention Policy
- [[departments]] — Compliance Department
- KYC Verification SOP (KYC-SOP-001)
- Account Opening SOP (ACCT-OPEN-SOP-001)
- Identification Document standards (Internal guidance)

---

## Document Version History

| Version | Date | Change | Author |
|---------|------|--------|--------|
| 2.1 | 2026-01-15 | Added risk-based re-verification schedule, EDD procedures | Compliance Head |
| 2.0 | 2025-07-01 | Annual review, updated PEP screening procedures | Compliance Head |
| 1.0 | 2024-01-01 | Initial policy | Compliance Head |

---

## Sign-Off

**Approved by:**  
Head of Compliance — **Date: 2026-01-15**  
Chief Risk Officer — **Date: 2026-01-15**  
Chief Executive Officer — **Date: 2026-01-15**

---

## Policy Statement (Executive Summary)

**Effective Date:** 2026-01-15  
**Last Updated:** 2026-01-15  
**Next Review:** 2027-01-15  

**Compliance Commitment:** FirstUK Bank is committed to preventing financial crime through robust customer identification and verification procedures. All staff are required to follow this policy without exception.

---
