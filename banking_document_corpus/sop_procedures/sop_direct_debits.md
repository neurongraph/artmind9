# FirstUK Bank — Direct Debit Management SOP

## Metadata

| Field | Value |
|-------|-------|
| Document ID | DDEBIT-SOP-001 |
| Version | 1.0 |
| Effective Date | 2026-01-15 |
| Review Date | 2027-01-15 |
| Owner | Head of Operations |
| Department | Operations, Payments |
| Status | Active |
| Classification | Internal |
| Audience | Operations Staff, Branch Staff, Customer Service |
| Related Documents | [[sop_standing_orders]], [[business_ontology]], [[systems]] |

---

## Purpose

Establish procedures for managing Direct Debits, including mandate collection, processing, dispute handling, and cancellation, ensuring compliance with Direct Debit Scheme rules and customer protection.

---

## Scope

Applies to:
- Direct Debit mandate collection and setup
- Direct Debit processing and execution
- Failed Direct Debit handling
- Customer disputes and claims
- Direct Debit cancellation

---

## Key Difference: Standing Order vs. Direct Debit

| Aspect | Standing Order | Direct Debit |
|--------|---|---|
| **Initiator** | You (customer) | Biller/Company |
| **Amount** | Fixed, same each time | Can vary (e.g., utility bill) |
| **Control** | You tell bank what to pay | Biller sends instructions |
| **Protection** | Limited | Full Direct Debit Guarantee |
| **Cancellation** | Anytime, immediate | Anytime, but 5-day notice typical |

---

## Process Overview

```
Biller Requests Authorization
    ↓
Customer Signs DD Mandate
    ↓
Mandate Setup in System
    ↓
Biller Submits Payment Instruction
    ↓
Payment Processed (Auto-debit)
    ↓
✅ Payment Complete
    ↓
Customer Can Dispute/Cancel Anytime
```

---

## Step 1: DD Mandate Collection

### Mandate Initiation

**Typical Scenario:**
- Customer signs up for utility bill, gym membership, insurance, etc.
- Biller asks: "Can we take payment by Direct Debit?"
- Customer agrees and signs mandate form

**Mandate Form Contains:**
- Bank account details (account number, sort code)
- Biller details (name, DDIC number)
- Amount type (fixed or variable)
- Customer authorization (signature)

### FirstUK Bank's Role

**When Mandate Reaches FirstUK:**
1. Mandate received from biller or customer
2. Account details verified (valid format, exists)
3. Customer identity confirmed
4. Mandate recorded in system
5. Biller notified of setup (ARUDD message)
6. Customer receives confirmation

**Note:** FirstUK doesn't issue the mandate; customer signs it and biller sends it to us

---

## Step 2: Mandate Setup in System

### System Entry

**Fields Recorded:**

| Field | Example |
|---|---|
| Mandate Reference | 12345678-1 |
| Customer Account | ACC-00001 |
| Biller Name | Electricity Co Ltd |
| Biller DDIC | 123456 |
| Biller Reference | CUST-98765 |
| Amount Type | Variable |
| Max Amount | Unlimited |
| Start Date | 2026-02-01 |
| Status | Active |
| Received Date | 2026-01-20 |

### Mandate Validation

**Checks Performed:**
- ✅ Account exists and is active
- ✅ Account holder matches mandate
- ✅ Biller DDIC valid (registered biller)
- ✅ Mandate form properly signed/authorized
- ✅ No duplicate mandates for same biller

**If Validation Fails:**
- Return mandate to sender
- Request corrected mandate
- Do not process until corrected

---

## Step 3: Payment Processing

### Biller Submission Process

**Biller Submits Payment Instruction:**
1. Biller determines amount due (e.g., £75 for monthly electricity)
2. Submits instruction via Direct Debit scheme
3. FirstUK receives instruction
4. FirstUK validates against mandate (amount within limits, account active)
5. Payment debited from account
6. Biller notified of success/failure

### Processing Rules

**Validation Before Debit:**
- ✅ Mandate active (not canceled)
- ✅ Amount doesn't exceed maximum (if set)
- ✅ Account has sufficient funds
- ✅ Instruction received in time window

**If Any Check Fails:**
- ❌ Debit rejected
- ❌ Biller notified (with reason)
- ❌ Customer may be charged (varies by biller)

### Timing

**Submission Window:** Billers submit 3 days before payment date  
**Processing:** FirstUK processes overnight (BACS)  
**Settlement:** Funds reach biller next business day

---

## Step 4: Failed Direct Debit

### Failure Scenarios

**Common Reasons:**
- Account closed
- Insufficient funds
- Account frozen
- Mandate canceled (customer didn't inform biller)

### Failure Process

**Step 1: Debit Attempt Fails**
- System blocks payment (insufficient funds, closed account, etc.)
- Failure recorded with reason

**Step 2: Notify Biller**
- Automated message sent (ARUDD rejection)
- Includes reason code (e.g., "insufficient funds")

**Step 3: Notify Customer**
- Email/SMS alert (optional, if opted in)
- Notification includes: Amount, biller, reason

**Step 4: Customer Action**
- Customer must:
  - Provide sufficient funds, OR
  - Contact biller to arrange alternative payment, OR
  - Cancel Direct Debit

**Step 5: Retry (At Biller's Option)**
- Biller may retry (usually within 3–5 days)
- Retry typically succeeds if funds now available

---

## Step 5: Direct Debit Disputes & Claims

### Customer Rights (Direct Debit Guarantee)

**If Unauthorized Debit:**
- Customer files claim with FirstUK
- FirstUK **refunds full amount within 10 business days**
- No questions asked
- No liability to customer

**Example Scenarios:**

**Scenario 1: Fraudulent Debit**
```
Unauthorized debit of £500 appears
Customer contacts bank: "I didn't authorize this!"
Bank refunds £500 within 10 days
Full protection under Direct Debit Guarantee
```

**Scenario 2: Wrong Amount Charged**
```
Biller charged £150 instead of £50
Customer contacts bank
Bank refunds £100 (overage) within 10 days
```

**Scenario 3: Charged After Cancellation**
```
Customer canceled Direct Debit
Biller still deducted £75 (error)
Bank refunds £75 within 10 days
```

### Claim Process

**Step 1: Customer Reports Issue**
- Phone: 0800-555-2265
- Email: support@firstuk.bank
- Branch: Visit in person

**Step 2: FirstUK Investigates**
- Verify Direct Debit details
- Confirm mandate status (active/canceled)
- Contact biller if needed
- Assess claim validity

**Step 3: Decision**
- **Approved:** Refund processed (within 10 days)
- **Denied:** Explanation provided to customer

**Step 4: Refund**
- Funds credited to account
- Confirmation sent to customer

### Dispute Resolution (if Customer Disagrees with Bank Outcome)

If customer unhappy with FirstUK's decision:
- Escalate to manager
- Formal complaint process (see [[policy_complaints]])
- Financial Ombudsman Service (if unresolved)

---

## Step 6: Mandate Cancellation

### How to Cancel

**Options:**
1. **Contact FirstUK Bank**
   - Phone: 0800-555-2265
   - Email: support@firstuk.bank
   - Branch: Visit in person
   - Online: Submit cancellation form (if available)

2. **Contact Biller**
   - Ask biller to cease collection
   - Biller may notify FirstUK automatically

### FirstUK Cancellation Process

**Upon Cancellation Request:**
1. Verify customer identity
2. Confirm mandate details
3. Set mandate status to "Canceled"
4. Notify biller (automated ARUDD cancellation)
5. Send confirmation to customer
6. Future payments blocked

**Effective Date:** Immediate (no notice period required)  
**Any Pending Payments:** Blocked if cancellation before settlement date

### Biller Cancellation Process

**If Biller Requests Cancellation:**
- Biller submits cancellation instruction
- FirstUK receives and processes
- Mandate status updated
- No further debits processed

---

## Step 7: Account Management

### Mandate During Account Changes

**If Customer Changes Address:**
- Biller notified automatically
- Mandate remains active
- No interruption to payments

**If Customer Gets New Account:**
- Old mandate canceled (if account closed)
- Customer must authorize new mandate for new account
- Biller must have new account details

**If Account Frozen (Fraud Hold):**
- Direct Debits blocked temporarily
- Biller notified (generic "unable to process" message)
- Customer resolution required before payment resumes

---

## Quality Assurance

### Weekly Review

**Operations Staff Checks:**
- Total value of Direct Debits processed
- Number of failed collections
- Customer disputes received
- Biller complaints

**Reconciliation:**
- DD transactions match biller instructions
- No duplicate payments
- Failed DDs investigated

### Audit Trail

**System Records:**
- All mandates (active and canceled)
- All payment instructions received
- All successful and failed debits
- All customer disputes and outcomes
- Customer communications

**Retention:** 6 years (per POCA regulations)

---

## Related Documents

- [[sop_standing_orders]] — Standing Orders (different mechanism)
- [[business_ontology]] — Direct Debit entity definition
- [[systems]] — Payment Processing System
- [[policy_complaints]] — Complaint procedures

---

## Document Version History

| Version | Date | Change | Author |
|---------|------|--------|--------|
| 1.0 | 2026-01-15 | Initial SOP | Head of Operations |

---

## Sign-Off

**Approved by:**  
Head of Operations — **Date: 2026-01-15**  
Head of Compliance — **Date: 2026-01-15**

---
