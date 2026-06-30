# FirstUK Bank — KYC Verification SOP

## Metadata

| Field | Value |
|-------|-------|
| Document ID | KYC-SOP-001 |
| Version | 2.0 |
| Effective Date | 2026-01-15 |
| Review Date | 2027-01-15 |
| Owner | Head of Compliance |
| Department | Compliance |
| Status | Active |
| Classification | Internal |
| Audience | Compliance Staff, Branch Staff, Operations |
| Related Documents | [[policy_customer_identification]], [[policy_aml]], [[business_ontology]] |

---

## Purpose

Establish procedures for conducting Know Your Customer (KYC) verification to confirm customer identity and address at account opening and periodically thereafter, ensuring compliance with FCA COBS Part 10 and Money Laundering Regulations 2017.

---

## Scope

Applies to:
- All customer account openings (new customers)
- All channels (branch, online, postal)
- Periodic re-verification (per risk-based schedule)
- Joint account holders (both must verify)

---

## Process Overview

```
Customer Provides Information
    ↓
Step 1: Document Collection
    ↓
Step 2: Document Verification
    ↓
Step 3: Address Verification
    ↓
Step 4: Risk Assessment
    ↓
Step 5: System Recording
    ↓
Step 6: Quality Check
    ↓
✅ KYC Complete & Approved
or
❌ KYC Failed - Escalate
```

**Timeline:** 1–2 business days

---

## Step 1: Document Collection

### Identity Document Request

**What to Request:**
- "Please provide a valid form of photo identification (UK Passport or Driving License)"

**Acceptable Documents (Primary):**
1. **UK Passport**
   - Must be valid (not expired)
   - Photo page clear
   - All security features visible

2. **UK Driving License**
   - Photocard format only
   - Must be valid (not expired)
   - Signature visible
   - Driving record intact

**Unacceptable Documents:**
- Expired documents
- Damaged/illegible documents
- Provisional licenses
- Non-UK documents (unless verified via secondary source)

### Address Document Request

**What to Request:**
- "Please provide a document confirming your current address"

**Acceptable Documents (Primary - Dated Within 3 Months):**
1. **Utility Bill** (Gas, Electricity, Water, Telephone)
2. **Council Tax Document** (Bill or banding notice)
3. **Bank Statement** (Another bank's statement acceptable)
4. **Mortgage Statement**

**Acceptable Documents (Secondary):**
- Tenancy agreement (signed + landlord details)
- Insurance policy (buildings/contents)
- HM Revenue & Customs letter
- Electoral register extract

---

## Step 2: Document Verification (Branch)

### Physical Document Examination

**Verification Steps:**

1. **Examine Original**
   - Request customer bring original document
   - Inspect document in person
   - Verify authenticity (security features, watermarks)
   - Check document not tampered with

2. **Photo Comparison**
   - Compare photo on ID to customer's appearance
   - Verify face matches
   - Note any discrepancies

3. **Signature Verification**
   - Compare signature on ID to customer's signature on form
   - Verify signature consistency

4. **Expiry Check**
   - Confirm document not expired
   - Note expiry date in record

5. **Data Recording**
   - Record document type (Passport, Driving License)
   - Record document number
   - Record issue date and expiry date
   - Note verification method ("in-branch examination")

**Pass Criteria:**
- ✅ Document valid and not expired
- ✅ Photo matches customer appearance
- ✅ Signature matches application
- ✅ No tampering or damage
- ✅ All security features present

**Fail Criteria:**
- ❌ Document expired
- ❌ Photo doesn't match (identity issue)
- ❌ Document damaged/unclear
- ❌ Customer unable to provide original

---

## Step 3: Address Verification

### Document Examination

1. **Check Address Format**
   - Verify UK postcode format: A(1,2)9(1,2)A 9A(2)
   - Confirm address is in United Kingdom
   - Note any address concerns

2. **Match Address Documents**
   - Compare address on ID with address on secondary document
   - Confirm both show same address
   - If addresses differ: Investigate (recent move? spelling variance?)

3. **Document Currency**
   - Utility bill, council tax: Dated within 3 months
   - Bank statement: Current (within last 3 months)
   - Tenancy agreement: Must be signed and current
   - Insurance policy: Must be active policy

**Pass Criteria:**
- ✅ Address on ID matches secondary document
- ✅ Address is in UK
- ✅ Secondary document is current (within 3 months)
- ✅ Address format valid (proper postcode)

**Fail Criteria:**
- ❌ Addresses don't match
- ❌ Document outdated (>3 months old)
- ❌ Non-UK address
- ❌ Invalid postcode format

---

## Step 4: Risk Assessment

### Customer Risk Level

**Determine Risk Category:**

**Low Risk:**
- UK national or long-term resident
- Age 18–65
- Standard product (SmartSaver, Current Account)
- UK address
- No PEP/adverse media
- Standard initial deposit (<£100k)

**Medium Risk:**
- Non-UK resident (but EU/developed country)
- Age >65 or <25
- Higher-value account (>£50k)
- Foreign address but EU/developed country
- Professional/business account

**High Risk:**
- Non-UK national with foreign address
- PEP (Politically Exposed Person)
- Adverse media (criminal history, fraud)
- Beneficial ownership unclear
- High-value account (>£250k)
- Country with weak AML controls

### Enhanced Due Diligence (EDD) Flag

**If High-Risk:**
- Mark customer for Enhanced Due Diligence
- Escalate to Compliance Head
- Additional verification may be required (beneficial ownership, source of funds)
- Compliance Head approval required before account creation

---

## Step 5: System Recording

### Document Entry in AMS

**Fields to Complete:**
- Customer ID (linked)
- Document Type (Passport / Driving License)
- Document Number
- Issue Date
- Expiry Date
- Verification Method (In-branch examination / Online upload / Postal)
- Verification Date
- Verified By (staff member name/ID)
- Status (Verified / Pending / Failed)

**Address Entry:**
- Address Line 1
- Address Line 2 (if applicable)
- City/Town
- Postcode
- Country
- Address Type (Registered / Correspondence)
- Address Verification Date
- Document Type (Utility Bill / Council Tax / etc.)

**Risk Assessment Entry:**
- Risk Level (Low / Medium / High)
- EDD Flag (Yes / No)
- Reason (if EDD flagged)
- Next Review Date (per risk-based schedule)

---

## Step 6: Quality Check

### Verification Quality Checklist

**Before Approving KYC:**

- [ ] Identity document collected and verified (original or certified copy)
- [ ] Document not expired
- [ ] Address document collected (current, within 3 months)
- [ ] Addresses match (ID and secondary)
- [ ] Photo matches customer appearance (or certified match)
- [ ] All fields recorded in AMS
- [ ] Risk assessment completed
- [ ] EDD flagged (if high-risk)
- [ ] No compliance concerns noted

**Manager Sign-Off:**
- Operations Manager or Compliance Staff reviews checklist
- Approves or requests additional verification
- Files checklist in account record

---

## Step 7: Periodic Re-Verification

### Risk-Based Re-Verification Schedule

**Low-Risk Customers:**
- Re-verify every 5 years
- Reminder generated automatically in system
- May be waived if customer activity indicates no risk increase

**Medium-Risk Customers:**
- Re-verify every 3 years
- Quarterly risk assessment (ongoing monitoring)
- Escalate if risk factors change

**High-Risk Customers:**
- Re-verify annually
- Quarterly enhanced monitoring
- Transaction-by-transaction review for suspicious patterns
- Escalate for any risk changes

**Triggering Immediate Re-Verification:**
- Customer registers new address
- Customer involvement in dispute/complaint
- Fraud detection/alert
- Regulatory query
- PEP status change
- Sanctions list hit

### Re-Verification Process

- Same as initial verification (Steps 1–6 above)
- Customer contacted for updated documents
- Documents verified and system updated
- Re-verification date recorded

---

## Online KYC Verification

### Digital Document Upload

**For Online Account Opening:**

1. **Upload Document**
   - Customer uploads photo of ID (Passport or Driving License)
   - System performs automated verification (if available)
   - Manual review by Compliance staff

2. **Verification Steps**
   - Check document not expired
   - Verify document number format
   - Review uploaded image quality
   - Flag for manual review if unclear

3. **Address Verification**
   - Customer enters address in form
   - Postcode verification (automated)
   - Secondary document upload (optional, if required)
   - Compliance staff review

4. **Approval**
   - Compliance staff approves or requests clarification
   - If approved, account proceeds to AML screening
   - If rejected, customer notified (not a regulatory reason disclosed)

**Timeline:** <1 hour (if no issues), 1–2 business days (if manual review)

---

## Postal KYC Verification

### Certified Copy Requirements

**Customer Provides:**
- Certified copy of ID (notarized by solicitor, notary public)
- Certified copy of address document

**Certification Process:**
- Solicitor/notary verifies identity
- Confirms document is genuine copy
- Stamps/signs certification
- Provides certification date

**Verification by Bank:**
- Operations staff reviews certification
- Confirms certifier details (notary/solicitor)
- May contact certifier to verify (if concerns)
- Records in system

**Timeline:** 3–5 business days

---

## Escalation & Exceptions

### When to Escalate

**Escalate to Compliance Head If:**
- ❌ Customer unable to provide acceptable ID
- ❌ Addresses don't match (possible fraud)
- ❌ Document appears forged/tampered
- ❌ Customer identity unclear
- ❌ High-risk customer (PEP/adverse media)
- ❌ Customer refuses to provide verification

### Exception Process

1. Document reason for exception
2. Escalate to Compliance Head
3. Compliance Head reviews and decides:
   - Approve (with additional conditions)
   - Request alternative verification
   - Reject (account application denied)
4. Decision recorded in system

---

## Training & Competency

**Staff Required Training:**
- Document authentication (spotting fakes, security features)
- Customer communication (explaining KYC requirements clearly)
- System data entry (accurate recording)
- Compliance procedures (what to escalate)

**Certification:**
- Pass knowledge assessment
- Practical observation (verified by manager on 5+ customers)
- Annual refresher training

---

## Related Documents

- [[policy_customer_identification]] — KYC Policy (detailed requirements)
- [[policy_aml]] — AML Policy (screening that follows KYC)
- [[sop_account_opening]] — Account opening (KYC is Step 3)
- [[business_ontology]] — Customer and Identification Document entities

---

## Document Version History

| Version | Date | Change | Author |
|---------|------|--------|--------|
| 2.0 | 2026-01-15 | Added online/postal procedures, enhanced due diligence | Head of Compliance |
| 1.0 | 2025-01-01 | Initial SOP | Head of Compliance |

---

## Sign-Off

**Approved by:**  
Head of Compliance — **Date: 2026-01-15**  
Chief Risk Officer — **Date: 2026-01-15**

---
