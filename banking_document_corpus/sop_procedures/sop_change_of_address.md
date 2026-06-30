# FirstUK Bank — Change of Address SOP

## Metadata

| Field | Value |
|-------|-------|
| Document ID | ADDR-SOP-001 |
| Version | 1.0 |
| Effective Date | 2026-01-15 |
| Review Date | 2027-01-15 |
| Owner | Head of Operations |
| Department | Operations, Customer Service, Compliance |
| Status | Active |
| Classification | Internal |
| Audience | Operations Staff, Customer Service, Branch Staff, Compliance |
| Related Documents | [[policy_customer_identification]], [[policy_aml]], [[policy_privacy]], [[sop_account_opening]], [[sop_standing_orders]], [[sop_direct_debits]], [[business_ontology]], [[escalation_matrix]], [[incident_response_plan]] |

---

## Purpose

Establish procedures for processing customer address changes in a way that maintains regulatory compliance (KYC, AML, GDPR), updates all related systems and accounts, and minimizes disruption to customer services.

---

## Why Address Change Is Complex

Address change triggers multiple regulatory and operational requirements:

| Requirement | Reason | Impact |
|---|---|---|
| **KYC Re-verification** | Proof of address validity | Account functionality may be restricted |
| **AML Re-screening** | New address may be in sanctions country | Transaction blocks if sanctions match |
| **GDPR Compliance** | Ensure data accuracy | Customer rights (deletion, portability) |
| **Standing Order Updates** | Recipient address may change | Payment routing implications |
| **Direct Debit Updates** | Mandate tied to address | Payment processing |
| **Statement Delivery** | Physical mail routing | Customer communication interruption |
| **Tax/Regulatory** | Different jurisdictions | Tax reporting requirements |
| **Joint Accounts** | Both account holders | Whose address takes precedence? |
| **Multiple Accounts** | Customer may have >1 account | Each account needs updating |

---

## Scope

Applies to:
- Personal customers (individuals)
- Joint account holders
- All product types (savings, checking, mortgages)
- Both domestic (UK) and international address changes
- Temporary vs. permanent changes
- Address verification and re-verification

---

## Overview Process

```
Customer Requests Address Change
    ↓
Verify Customer Identity
    ↓
Identify All Affected Accounts
    ↓
Collect New Address Details
    ↓
Verify New Address (Proof of Address)
    ↓
Update KYC Records
    ↓
Assess AML Risk (New Address/Country)
    ↓
AML Screening (if high-risk)
    ↓
Update All Systems (Core, Standing Orders, Direct Debits, DW)
    ↓
Update Statement Delivery
    ↓
Update Communications (Email/Phone/Post)
    ↓
Notify Customer of Change
    ↓
Close Change Request
```

**Timeline:** 2-5 business days (depending on proof of address requirement)

---

## Step 1: Initiate Change Request

### How Customer Requests Change

**Channels:**
1. **Online Banking** — Self-service address change (if enabled)
2. **Branch Visit** — In-person update
3. **Phone** — Call customer service (0800-555-2265)
4. **Email** — support@firstuk.bank with details
5. **Post** — Complete form and mail back

### Initial Data Collection

When customer initiates change, collect:

| Field | Required | Notes |
|---|---|---|
| **Customer Name** | Yes | Must match account |
| **Account Number** | Yes | To identify which account(s) |
| **New Address** | Yes | Full postal address |
| **Address Type** | Yes | Permanent or temporary? |
| **Move Date** | Yes | When is customer moving? |
| **Reason** | Optional | For CRM tracking (relocation, holiday home, etc.) |
| **Phone Number** | Recommended | To confirm details |
| **Email** | Recommended | For notifications |

### Request Documentation

**Create Change Request:**
- [ ] Assign Change Request ID (e.g., ADDR-REQ-00001)
- [ ] Record timestamp (date/time received)
- [ ] Identify requesting channel
- [ ] Document all provided information
- [ ] Flag: Temporary vs. permanent

**Initial Validation:**
- [ ] Account exists in system
- [ ] Account status allows changes (not frozen/closed)
- [ ] Customer information matches (cross-check against KYC file)
- [ ] If joint account: Note both holders' addresses

---

## Step 2: Verify Customer Identity

### Authentication Requirements

**For Online Self-Service:**
- 2FA code sent to registered phone
- Customer enters code
- Biometric login (if enabled)
- **Risk Level:** Low (already authenticated in system)

**For Branch Visit:**
- Check government-issued ID (Passport/Driving License)
- Compare photo to customer
- Compare ID address to existing records
- Verify account holder relationship
- **Risk Level:** Low (in-person verification)

**For Phone/Email Request:**
- Verify using security questions
- Confirm date of birth
- Last 4 digits of account number
- Mother's maiden name (if on file)
- Confirm contact details on record
- **Risk Level:** Medium (remote verification)

### Escalation If Identity Cannot Be Verified
- Don't process change
- Request additional verification
- Escalate to manager if customer disputes
- Document reason for refusal
- Contact customer with next steps

---

## Step 3: Identify All Affected Accounts

### Single vs. Multiple Accounts

**Query System:**
- [ ] Check all accounts linked to customer
- [ ] Identify all product types (savings, checking, mortgages, cards)
- [ ] Note joint account holders (separate treatment)
- [ ] Identify linked accounts (e.g., mortgage on property at address)

**Example Scenarios:**

**Scenario A: Single Account (Typical)**
```
Customer has: 1 SmartSaver account
Action: Update single account address
Timeline: 2 days
```

**Scenario B: Multiple Accounts**
```
Customer has: 1 SmartSaver + 1 Current + 1 Mortgage
Decision Point: Same address for all, or different?
Action: Update each account based on customer instruction
Timeline: 3-5 days
```

**Scenario C: Joint Account**
```
Customer has: Joint SmartSaver with spouse
Issue: Whose address is primary?
Action: Confirm both account holders agree to change
Timeline: 3-5 days (need written consent from both)
```

**Scenario D: Mortgage with Property**
```
Customer has: Mortgage on property at old address
Issue: Mortgage is tied to property collateral
Action: Update personal address but keep property address in mortgage
Timeline: Special handling required
```

### Decision Matrix: Which Address to Update

| Account Type | Address To Use | Reason | Special Notes |
|---|---|---|---|
| **SmartSaver** | Personal residence | Statements sent here | Can be different per account |
| **Current** | Primary residence | Checks sent here | Usually same as SmartSaver |
| **Mortgage** | Property address (don't change) + Personal (change) | Property address locked to deed | Two separate fields |
| **Joint Account** | Confirm both holders' addresses | Statements to primary address | May differ for each holder |

### Document Findings

- [ ] List all affected accounts (e.g., ACC-00001, ACC-00002, ACC-00003)
- [ ] Note account types (savings/checking/mortgage)
- [ ] Note joint account holders (if any)
- [ ] Identify any complications (mortgage, joint, multiple addresses requested)
- [ ] Flag for escalation if needed

---

## Step 4: Collect & Validate New Address Details

### Address Information Required

**For UK Address:**

| Field | Format | Example | Required |
|---|---|---|---|
| **Property Number/Name** | Text or number | "24" or "Rosewood Cottage" | Yes |
| **Street Name** | Text | "Oak Lane" | Yes |
| **City/Town** | Text | "Manchester" | Yes |
| **Postcode** | Format: AA9A 9AA | "M1 1AA" | Yes |
| **County** | Text | "Greater Manchester" | No |

**For International Address:**

| Field | Format | Example | Required |
|---|---|---|---|
| **Full Address** | Per country format | Varies by country | Yes |
| **Country** | ISO country code | "US" for USA | Yes |
| **Postal Code** | Per country format | "10001" (USA) | Yes |
| **City** | Per country format | "New York" | Yes |

### Validation Checks

**UK Address Validation:**
- [ ] Postcode format correct (AA9A 9AA)
- [ ] Postcode matches city/town (use Royal Mail lookup)
- [ ] Address not on blacklist (fraudulent addresses)
- [ ] Property number/name is valid

**International Address Validation:**
- [ ] Country is real and recognized
- [ ] Postal code format correct for country
- [ ] Address not in sanctions country (see Step 5: AML)
- [ ] Address format matches country requirements

**Escalation If Validation Fails:**
- Don't accept invalid address
- Ask customer to re-confirm
- Provide postal code lookup tool
- Escalate if customer insists on non-standard address

### Data Entry

- [ ] Address entered exactly as customer provided (preserve formatting)
- [ ] Second person verifies entry for accuracy
- [ ] Customer confirms address is correct (phone/SMS/email)
- [ ] Address recorded in system

---

## Step 5: Assess AML Risk & Re-Screening

### Risk Assessment

**Questions to Answer:**

1. **Is the new address in a high-risk country?**
   - High-risk countries: Iran, North Korea, Syria, Crimea, Cuba, Sudan, etc.
   - Source: OFAC list, EU Sanctions, HMT Sanctions
   - If YES: Escalate to Compliance, may require enhanced due diligence

2. **Is the new address a known sanctions location?**
   - Example: Address matches OFAC-sanctioned entity address
   - If YES: Escalate immediately, block address change

3. **Does the address change create unusual profile?**
   - Example: Customer moves to high-crime area, or frequent international moves
   - If YES: Flag for ongoing monitoring

4. **Is this a temporary or permanent change?**
   - Temporary (holiday home): Lower risk
   - Permanent (migration): Higher risk, more screening needed

### Risk Scoring

| Risk Factor | Score | Action |
|---|---|---|
| **Low Risk** | <25 | Proceed with standard process |
| **Medium Risk** | 25–50 | Standard screening + confirm with customer |
| **High Risk** | 50–75 | Compliance review required |
| **Very High Risk** | 75+ | Escalate to Compliance Head, may block change |

**Example Risk Calculation:**
```
Base Risk: 0
+ UK domestic move: 0
+ Permanent change: 0
+ No sanctions history: 0
= Low Risk (0)
→ Proceed with standard process

---

Base Risk: 0
+ Move to high-risk country (Iran): +50
+ No compliance history: 0
+ First move abroad: +20
= High Risk (70)
→ Escalate to Compliance Head
→ May require Enhanced Due Diligence
```

---

## Step 6: Obtain & Verify Proof of Address

### When Proof of Address Is Required

**Always Required If:**
- Moving to international address (new country)
- Address is in high-risk jurisdiction
- Customer flagged as medium/high risk
- Compliance Head requests it
- Customer has been with bank <5 years

**May Be Waived If:**
- Domestic UK move (same country)
- Low-risk address
- Customer is low-risk (established, long-term)
- Address change within same local authority

### Acceptable Proof of Address Documents

**Tier 1 (Preferred):**
- Utility bill (gas, electricity, water) — must be dated within 3 months
- Council tax letter — current year
- Bank statement from another bank — dated within 3 months

**Tier 2 (Acceptable):**
- Mortgage statement — current year
- Tenancy agreement (must be certified by landlord or solicitor)
- Letter from employer — dated within 1 month
- Insurance policy document — current year

**Tier 3 (Not Acceptable Without Explanation):**
- Passport (doesn't have address)
- Driving license (old address may not match)
- Mobile phone bill (not reliable)
- P45/P60 tax forms (stale)

### Collection Process

**Option 1: In-Person (Branch)**
- Customer brings document to branch
- Staff member verifies original (check for tampering)
- Photocopy document
- File copy with address change request
- Confirm document details in system

**Option 2: Online Upload**
- Customer logs into online banking
- Uploads image/PDF of document
- System prompts for document type and date
- Compliance verifies upload legibility
- File stored securely

**Option 3: Email**
- Customer emails photo/scan to: kyc@firstuk.bank
- Subject: "Address Change - [Account Number]"
- Compliance verifies and files
- Confirm receipt to customer

**Option 4: Post**
- Customer mails original or certified copy
- FirstUK receives and verifies
- Document filed
- Original returned if requested
- Slower method (5–7 days)

### Verification Checklist

- [ ] Document received (note date)
- [ ] Document type identified
- [ ] Document date within acceptable range (usually 3 months)
- [ ] Customer name on document matches account
- [ ] Address on document matches new address provided
- [ ] Document is original or certified copy (not photocopy)
- [ ] No signs of tampering
- [ ] Document legibility sufficient to read
- [ ] Verified by second staff member (QA check)

### If Proof of Address Cannot Be Obtained

**Escalation Process:**
1. Don't block address change indefinitely
2. Process change with flag in system: "Proof of address pending"
3. Allow customer to use new address for communications
4. Set deadline (30 days) for document submission
5. Restrict account (e.g., limit transactions) if document not provided by deadline
6. Escalate to manager at 30 days

---

## Step 7: Update KYC Records

### KYC Data Update

**Update Customer Demographic File:**

| Field | Old Value | New Value | Notes |
|---|---|---|---|
| **Address Line 1** | [old] | [new] | Primary street address |
| **Address Line 2** | [old] | [new] | Apartment/suite (if applicable) |
| **City/Town** | [old] | [new] | Municipal location |
| **Postcode/ZIP** | [old] | [new] | Postal code format |
| **Country** | [old] | [new] | ISO country code |
| **Address Verification Date** | [old] | [today's date] | When proof was verified |
| **Address Verification Method** | [old] | [method] | In-person, online, post, etc. |
| **Verification Status** | [old] | Verified | Or "Pending" if proof outstanding |

### Risk Re-Assessment

**Recalculate Customer Risk Based on New Address:**

Previous Risk: [e.g., Low]  
New Risk: [e.g., Medium if moved abroad]  

**If Risk Category Changed Upward:**
- Flag for Compliance review
- May trigger enhanced due diligence
- Ongoing monitoring may increase
- Customer may be notified of account restrictions

### Address Change History

**Document in System:**

```
Address Change Log:
Timestamp: 2026-01-15 14:30 UTC
Old Address: 123 Oak Lane, Manchester, M1 1AA, UK
New Address: 456 Elm Street, New York, NY 10001, USA
Change Type: Permanent relocation
Initiated By: Customer (online)
Verified By: John Smith (CSR)
Proof of Address: Utility bill (dated Jan 10, 2026)
KYC Status: Verified
AML Status: Cleared (US address, low-risk)
Change Request ID: ADDR-REQ-00001
Status: Approved
```

**Keep 6-Year Audit Trail** (per [[policy_retention]])

---

## Step 8: AML Re-Screening (If Required)

### When to Perform Full AML Screening

**Mandatory Screening If:**
- Customer moving to new country (not just new city)
- New country is on OFAC/EU/HMT/UN sanctions list
- Customer flagged as medium/high risk
- Compliance Head requires it
- Address is in jurisdictions: Iran, Syria, North Korea, Cuba, etc.

**Optional Screening If:**
- Customer moving within UK
- Low-risk customer
- Existing banking history >5 years with no issues

### Screening Process

**Steps:**

1. **Extract Screening Data**
   - Customer name (from KYC)
   - New address (country, city, postcode)
   - DOB and nationality
   - Customer ID

2. **Screen Against Lists**
   - OFAC (US sanctions)
   - EU Consolidated List (EU sanctions)
   - HMT Sanctions (UK Home Treasury)
   - UN Sanctions
   - PEP Database (Politically Exposed Persons)
   - Adverse Media (news-based screening)

3. **Review Results**
   - No match found: **CLEAR** — proceed with address change
   - Partial match: Review name, DOB, address to confirm false positive
   - Exact match: **ESCALATE IMMEDIATELY** to Compliance — block address change

4. **Document Screening**

```
Screening Result:
Date Screened: 2026-01-15
Screening Lists: OFAC, EU, HMT, UN, PEP
Result: CLEAR (no matches)
Screened By: Compliance System (automated)
Reviewed By: Sarah Johnson (Compliance Officer)
Status: APPROVED
```

### If Match Found (True Positive)

**Immediate Actions:**
1. DO NOT process address change
2. DO NOT notify customer (tipping-off is illegal)
3. Escalate to Head of Compliance immediately
4. Freeze account (no new transactions)
5. File Suspicious Activity Report (SAR) to FCA
6. Await FCA guidance before proceeding

**Timeline:** FCA response typically 10–30 days

### If False Positive

**Investigation:**
- Does name match exactly? (Different spelling?)
- DOB match? (Different person?)
- Address match? (Different country, different person?)
- Middle names or aliases? (Eliminate confusion)

**Resolution:**
- Document false positive reason
- Record in system ("False positive - different DOB")
- Proceed with address change
- Update screening rules to reduce future false positives

---

## Step 9: Update All Systems

### Core System Update (AMS)

**Account Management System:**

```
Field: Customer Address
Old Value: 123 Oak Lane, Manchester, M1 1AA, UK
New Value: 456 Elm Street, New York, NY 10001, USA
Update Timestamp: 2026-01-15 15:00 UTC
Updated By: CSR_John_Smith
Change Type: ADDR_CHANGE
Reason: Customer relocation
```

**Timeline:** Immediate (within minutes of approval)

**Verification:**
- [ ] Check system reflects change
- [ ] Confirm all account fields updated
- [ ] Verify change appears in customer view (online banking)
- [ ] Test: Run customer query to confirm address

### Standing Order System Update

**If Customer Has Standing Orders:**

**Question:** Do standing orders have recipient addresses that need updating?

**Scenario A: Standing Order to Another FirstUK Bank Account**
- No action needed
- Standing order uses account numbers, not addresses
- Continue processing normally

**Scenario B: Standing Order to External Bank (Recipient May Have Moved)**
- Ask customer: "Is recipient's address still correct?"
- If YES: No change needed
- If NO: Customer must update recipient address (separate process)
- Document in standing order: "Recipient address updated by customer [date]"

**Scenario C: Standing Order FROM This Customer to External Party**
- No recipient address change needed
- Sender's address change doesn't affect standing order
- Continue processing

---

### Direct Debit System Update

**If Customer Has Direct Debits:**

**Mandate Address Field:**
- Direct Debit mandates may have customer address field
- Update customer address in mandate record
- May require confirmation to biller (automated ARUDD message)

**Notification to Billers:**
- System automatically notifies billers of address change
- Billers update their records
- No interruption to DD payments expected

**Actions:**
- [ ] Identify all active Direct Debits
- [ ] Update customer address in mandate file
- [ ] Send automated ARUDD update to billers
- [ ] Confirm updates processed (check response messages)

---

### Statement & Communication Address Update

**Update Delivery Preferences:**

| Delivery Type | Update Required | Action |
|---|---|---|
| **Physical Statements** | YES | Update mailing address |
| **Bank Statements** | YES | Statements will go to new address |
| **Tax Forms (1099, etc.)** | YES | If international move, may need separate tax address |
| **Correspondence** | YES | All letters to new address |
| **Notice of Change** | YES | Notification letter sent to old address (GDPR) |

**Email/Phone:**
- [ ] Confirm email address (no change needed unless customer requests)
- [ ] Confirm phone number (no change needed unless customer requests)
- [ ] Update contact method preference (email vs. post)

### Data Warehouse Update

**Nightly Batch Process:**
- [ ] Address change pushed to Data Warehouse
- [ ] Historical address kept (audit trail)
- [ ] New address reflects in analytics and reporting
- [ ] Customer segmentation updated (e.g., if international, flag for international monitoring)

**Timeline:** Synchronized with nightly ETL (typically 23:00–03:00 UTC)

---

## Step 10: Special Scenarios & Escalations

### Scenario 1: Joint Account — Holders at Different Addresses

**Situation:** Joint account holders want different addresses (e.g., divorced but still joint account)

**Complications:**
- Which address for statements?
- Which address for tax purposes?
- Different countries may mean different regulatory requirements

**Resolution Process:**

1. **Verify both account holders consent** (written consent required)
2. **Determine primary address** (for statements, usually first account holder)
3. **Update secondary address** (for legal/tax purposes if needed)
4. **Obtain proof of address for both** (if either moved internationally)
5. **Re-screen both** (if new addresses in high-risk jurisdictions)
6. **Document both addresses in system**
7. **Explain potential tax implications** (may need tax ID update)

**Example:**
```
Joint Account: ACC-00001 (John Smith & Jane Smith)
John's Address: 123 Oak Lane, Manchester, M1 1AA, UK
Jane's Address: 456 Elm Street, London, E1 1AA, UK

Primary Address (Statements): John's (Manchester)
Secondary Address (Legal): Jane's (London)

Both must consent to change. Both addresses verified independently.
```

---

### Scenario 2: Temporary Address Change (Holiday Home)

**Situation:** Customer moving to holiday home for 3 months, wants to update address temporarily

**Approach:**

**Option A: Update as Permanent (Simpler)**
- Update to new address
- When customer returns, submit another address change
- Simpler but creates extra work

**Option B: Mark as Temporary (Better)**
- Note in system: "Temporary address change until [date]"
- Set reminder to contact customer on return date
- Ask: "When you return, shall we change back?"
- Reduces address change requests

**Implementation:**
```
Address Change Type: TEMPORARY
Old Address: 123 Oak Lane, Manchester, M1 1AA
Temporary Address: 789 Beach Road, Marbella, Spain
Effective Date: 2026-06-01
Return Date: 2026-09-01
Revert To: Manchester address
Contact Before: 2026-08-25 (reminder for customer)
```

---

### Scenario 3: International Move with Mortgage

**Situation:** Customer moves abroad but maintains UK mortgage on property

**Complexity:** 
- Personal address changes to international
- Property address (mortgage collateral) stays in UK
- Different regulatory treatment

**Approach:**

1. **Separate Address Fields**
   - Personal Address: Update to new country
   - Property Address: Keep unchanged (locked to deed)
   - Mortgage Address: Remains as collateral location

2. **Tax Implications**
   - International move may trigger tax residency questions
   - May need tax identification number in new country
   - Escalate to Compliance/Tax

3. **Statement Delivery**
   - Statements go to personal address (new country)
   - Tax forms (if applicable) may go to mortgage property address
   - Clarify with customer

4. **Regulatory Screening**
   - Full AML re-screening (new country)
   - May trigger enhanced due diligence
   - Compliance review required

**Example:**
```
Customer: James Wilson
Mortgage: Property at 123 Oak Lane, Manchester (UK) — DO NOT CHANGE
Personal Address: Moving to 456 Rue de Paris, Paris, France

Update:
- Personal Address: 456 Rue de Paris, Paris, France, FR
- Mortgage Property Address: 123 Oak Lane, Manchester, M1 1AA, UK (UNCHANGED)
- Statements: Send to Paris address
- Mortgage Documents: May continue to reference Manchester property

Actions:
- Full AML re-screening (France is low-risk, but flag for ongoing monitoring)
- Compliance review (international move)
- Tax confirmation (may need French tax ID)
- Escalation to Compliance Head
```

---

### Scenario 4: Address Change Fraud Risk

**Situation:** Address change request from customer account that looks suspicious

**Red Flags:**
- Multiple address changes in short period
- Address changes to known fraud locations
- Authorized by staff member with access issues
- Customer disputes the change later

**Escalation:**
- Flag as potential fraud/account takeover
- Verify customer identity with additional authentication
- Review recent account activity
- Ask customer: "Did you request this change?"
- File exception/incident if fraudulent change confirmed
- See [[policy_fraud]]

---

### Scenario 5: Deceased Customer (Address Change After Death)

**Situation:** Executor or family member requests address change for deceased customer's account

**Process:**
- This is account MANAGEMENT, not address change
- Route to probate/estate team
- Requires death certificate
- Account may be frozen during estate settlement
- Not covered by standard address change SOP
- Escalate to Head of Operations

---

## Step 11: Notify Customer

### Notification Methods

**Primary: Email (Preferred)**

```
Subject: Your Address Change Confirmation — FirstUK Bank

Dear [Customer Name],

Your address has been successfully updated with FirstUK Bank.

Old Address:
123 Oak Lane
Manchester, M1 1AA
United Kingdom

New Address:
456 Elm Street
New York, NY 10001
USA

Effective Date: January 15, 2026

What Happens Next:
- Your statements will be sent to your new address starting [date]
- All correspondence will use your new address
- Your account remains fully active
- Standing orders and Direct Debits continue normally

If You Didn't Request This Change:
Contact us immediately: 0800-555-2265 (fraud line)

Questions?
- Phone: 0800-555-2265
- Email: support@firstuk.bank
- Online Chat: www.firstuk.bank

Thank you,
FirstUK Bank Customer Service
```

**Secondary: SMS (If International Move)**

```
FirstUK: Your address has been changed to [NEW CITY/COUNTRY]. 
Statements will go to new address. Questions? Call 0800-555-2265. 
If you didn't request this, call immediately.
```

**Tertiary: Letter to Old Address (GDPR Requirement)**

**Why?** To verify customer actually moved (detect account takeover fraud)

```
Dear [Customer Name],

We are writing to confirm that your address with FirstUK Bank 
has been updated from:

[OLD ADDRESS]

to:

[NEW ADDRESS]

Effective Date: [date]

If you did NOT request this change, or if you believe this is 
fraudulent, contact us immediately:

Phone: 0800-555-2265 (24/7 Fraud Line)
Email: support@firstuk.bank

This letter is sent to your old address as a security check.

Best regards,
FirstUK Bank
```

---

### Confirmation to Customer

**Provide Confirmation Details:**

- [ ] Confirmation email sent (note date/time)
- [ ] SMS sent (if international)
- [ ] Letter sent to old address (if for fraud detection)
- [ ] Change Request ID provided (for reference)
- [ ] Timeline for completion explained (2-5 business days)
- [ ] What to expect (statements go to new address starting [date])

---

## Step 12: Quality Assurance & Closure

### QA Checklist

**Before Closing Change Request:**

- [ ] Customer identity verified ✓
- [ ] New address validated (format, postcode) ✓
- [ ] AML risk assessed ✓
- [ ] AML screening completed (if required) ✓
- [ ] Proof of address obtained & verified (if required) ✓
- [ ] KYC records updated ✓
- [ ] Core system (AMS) updated ✓
- [ ] Standing orders reviewed & updated (if needed) ✓
- [ ] Direct Debits reviewed & updated (if needed) ✓
- [ ] Statement delivery updated ✓
- [ ] Data warehouse updated ✓
- [ ] Customer notified (email, SMS, letter) ✓
- [ ] Change documented in system ✓
- [ ] No exceptions/escalations outstanding ✓

**If Any Item Not Checked:**
- Don't close change request
- Complete outstanding items
- Document reason for delay
- Escalate if blocked >3 business days

---

### Final Documentation

**Complete Change Request File:**

```
Change Request ID: ADDR-REQ-00001
Status: COMPLETED
Date Created: 2026-01-15
Date Completed: 2026-01-17 (2 business days)

Customer: John Smith
Account(s): ACC-00001 (SmartSaver)
Old Address: 123 Oak Lane, Manchester, M1 1AA, UK
New Address: 456 Elm Street, New York, NY 10001, USA

Approvals:
- Customer Identity Verified: ✓ John Smith (2026-01-15)
- New Address Validated: ✓ Sarah Johnson (2026-01-15)
- AML Risk Assessment: Low (domestic to international, but US is low-risk)
- AML Screening: Completed, CLEAR (2026-01-15)
- Proof of Address: US utility bill verified (2026-01-16)
- KYC Update: Completed (2026-01-16)
- System Update: Completed (2026-01-16)
- Customer Notification: Email sent (2026-01-16)

Timeline: 2 business days
Exceptions: None
Escalations: None

Closed By: John Smith (CSR)
Date Closed: 2026-01-17
```

**Archive File:** Retain per [[policy_retention]] (6 years)

---

### Close Change Request

**System Status Update:**
- [ ] Mark Change Request as "COMPLETED"
- [ ] Remove from open queue
- [ ] Archive for 6-year retention
- [ ] Send confirmation to customer (final notification)

---

## Escalation Triggers

### Escalate to Manager If:
- Customer disputes address change
- Multiple address changes in short period
- Address is in high-risk country
- Proof of address cannot be obtained (timeout >30 days)
- AML screening produces match (to Compliance Head)
- Joint account holders dispute change

### Escalate to Compliance Head If:
- AML screening produces true positive match
- International move to sanctions country
- Customer flagged as medium/high risk
- Address change suspected fraud

### Escalate to Head of Operations If:
- Process blocked >5 business days
- Technical system issue preventing update
- Multiple accounts with conflicting requirements
- Regulatory query related to address change

---

## Common Mistakes (Don't Make These!)

❌ **Mistake 1:** Accept address change without proof of address for international move  
✅ **Correct:** Always verify proof of address for international moves

❌ **Mistake 2:** Skip AML screening for address to new country  
✅ **Correct:** Always screen if customer moves to new country

❌ **Mistake 3:** Update address without verifying customer identity  
✅ **Correct:** Always verify identity before processing change

❌ **Mistake 4:** Change joint account to one holder's address without consent from both  
✅ **Correct:** Obtain written consent from both joint account holders

❌ **Mistake 5:** Send statement to new address before confirming delivery capability  
✅ **Correct:** Verify address is valid before changing statement delivery

❌ **Mistake 6:** Notify customer of address change before internal systems updated  
✅ **Correct:** Complete all system updates first, then notify

❌ **Mistake 7:** Ignore red flags (multiple changes, fraud indicators)  
✅ **Correct:** Escalate any suspicious address changes immediately

---

## Related Documents

- [[policy_customer_identification]] — KYC verification procedures
- [[policy_aml]] — AML screening procedures and sanctions lists
- [[policy_privacy]] — GDPR data accuracy and customer rights
- [[policy_retention]] — Document retention schedules (6-year minimum)
- [[sop_account_opening]] — Account opening for reference (initial address collection)
- [[sop_standing_orders]] — Standing order address implications
- [[sop_direct_debits]] — Direct Debit mandate address updates
- [[business_ontology]] — Address entity definition
- [[escalation_matrix]] — Escalation authority by amount/issue
- [[incident_response_plan]] — How to respond if fraud detected

---

## Document Version History

| Version | Date | Change | Author |
|---------|------|--------|--------|
| 1.0 | 2026-01-15 | Initial SOP for change of address | Head of Operations |

---

## Sign-Off

**Approved by:**  
Head of Operations — **Date: 2026-01-15**  
Head of Compliance — **Date: 2026-01-15**  
Chief Customer Officer — **Date: 2026-01-15**

---

**This SOP is mandatory for all staff processing address changes. Non-compliance may result in regulatory fines and customer fraud.**

---
