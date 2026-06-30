# FirstUK Bank — Exception Handling Guide

## Metadata

| Field | Value |
|-------|-------|
| Document ID | EXCP-SOP-001 |
| Version | 1.0 |
| Effective Date | 2026-01-15 |
| Review Date | 2027-01-15 |
| Owner | Head of Operations |
| Department | Operations |
| Status | Active |
| Classification | Internal |
| Audience | Operations Staff, Customer Service, Compliance |
| Related Documents | [[escalation_matrix]], [[policy_complaints]], [[business_ontology]] |

---

## Purpose

Establish procedures for identifying, investigating, and resolving operational exceptions (failed transactions, disputed transactions, regulatory exceptions) to ensure customer satisfaction and regulatory compliance.

---

## Scope

Applies to:
- Failed transactions (deposits, withdrawals, transfers)
- Disputed transactions (customer claims unauthorized)
- Regulatory exceptions (compliance failures, audit issues)
- Exception escalation and resolution

---

## Exception Categories

### Category 1: Transaction Failures

**Definition:** Transaction initiated but not completed successfully

**Examples:**
- Bank transfer fails (recipient account invalid)
- ATM withdrawal denied (insufficient funds)
- Standing order fails (account closed)
- Direct Debit rejected (no funds)

**Customer Impact:** High (customer expecting transaction to complete)  
**SLA:** Resolve within 1–3 business days

---

### Category 2: Disputed Transactions

**Definition:** Customer claims transaction unauthorized or incorrect

**Examples:**
- "I didn't authorize this transfer"
- "I was charged twice"
- "Amount is wrong (£50 instead of £5)"
- "Transaction shows wrong date"

**Customer Impact:** High (financial loss claim)  
**SLA:** Investigate within 5–10 business days

---

### Category 3: Regulatory Exceptions

**Definition:** Operational or compliance issue flagged by audit/risk

**Examples:**
- Missing KYC documentation
- AML screening delay
- Transaction outside policy limits
- Regulatory reporting error

**Customer Impact:** Low (internal issue, may require account restriction)  
**SLA:** Remediate within 5–30 days (depends on severity)

---

## Exception Detection

### Automated Detection

**System Monitors For:**
- Failed transaction attempts (system logs)
- Unusual transaction patterns (fraud rules)
- Regulatory alerts (compliance flagged)
- KYC/AML exceptions (verification issues)

**Action:** Automatic logging to exception queue

### Manual Detection

**Staff Reports:**
- Customer complaint (phone, email, branch)
- Teller observation (transaction rejected)
- Audit finding (control testing)
- Regulator communication (regulatory exception)

**Action:** Logged by staff, escalated to exception team

---

## Step 1: Triage & Categorization

### Receipt

When exception reported:
1. **Log Immediately**
   - Exception ID generated (EXC-00001234)
   - Date/time recorded
   - Reporter name noted
   - Initial description captured

2. **Categorize**
   - Is it a failed transaction? → Category 1
   - Is it a customer dispute? → Category 2
   - Is it regulatory? → Category 3

3. **Assess Severity**
   - Amount involved
   - Customer impact
   - Regulatory implication
   - Escalation required?

---

## Step 2: Investigation

### Category 1: Failed Transactions

**Investigation Steps:**
1. Pull transaction record from system
2. Check status (why did it fail?)
3. Verify customer authorization
4. Confirm account details correct
5. Assess if reversible

**Common Failure Reasons & Resolution:**

| Reason | Investigation | Resolution |
|--------|---|---|
| Insufficient funds | Confirm balance at time of attempt | Explain to customer, offer retry once funds available |
| Invalid recipient | Check account number format | Contact customer for correct details, retry |
| Account frozen | Confirm freeze in place and reason | Unfreeze if valid, notify customer |
| Duplicate prevention | Check for earlier same transaction | Explain duplicate rule, offer manual retry |

**Outcome:** Refund if funds were debited + failed, retry if appropriate, or explain to customer

### Category 2: Disputed Transactions

**Investigation Steps:**
1. Pull transaction details
2. Verify customer authorized
3. Check timestamp and amount
4. Review customer history (patterns?)
5. Assess fraud likelihood

**Questions to Answer:**
- Did customer authorize this amount?
- To whom was money sent? (known party?)
- Was customer negligent? (shared password?)
- Timeline: When reported vs. when occurred?

**Outcomes:**
- **Unauthorized:** Full refund within 10 days
- **Authorized:** Explain, educate customer
- **Disputed:** Further investigation or escalation

### Category 3: Regulatory Exceptions

**Investigation Steps:**
1. Identify specific regulatory issue
2. Assess impact (customer-facing vs. internal)
3. Determine remediation steps
4. Check if other customers affected
5. Document for audit trail

**Examples & Remediation:**

| Exception | Issue | Action |
|---|---|---|
| Missing KYC | Account opened without ID verification | Obtain missing docs immediately, suspend if can't provide |
| AML delay | Customer screened 3 days after opening | Retroactive screening, escalate if flagged |
| Rate error | Wrong interest applied to account | Recalculate, add back-interest if in customer's favor |

---

## Step 3: Resolution

### Failed Transactions

**Resolution Options:**

**Option A: Reprocess**
- Confirm funds now available
- Resubmit transaction
- Confirm success to customer
- Close exception

**Option B: Refund + Compensation**
- If funds were debited despite failure: Refund immediately
- Add interest (if applicable)
- Apologize for inconvenience
- Consider compensation (if service failure)

**Option C: Manual Facilitation**
- Customer wants to proceed but system blocks
- Staff can manually facilitate (with approvals)
- Document reason for manual override

### Disputed Transactions

**Resolution Options:**

**Option A: Refund**
- Customer proves unauthorized OR
- Merchant error confirmed
- Refund within 10 business days
- Add interest if delayed
- Apologize

**Option B: Educate & Retain**
- Customer authorized but regrets
- Explain terms of service
- No refund (customer's decision)
- Offer to set up safeguards (daily limits, alerts)

**Option C: Escalate to Merchant**
- Payment dispute (chargeback claim)
- Escalate to card scheme
- Merchant investigation initiated
- Resolution per scheme rules

### Regulatory Exceptions

**Resolution Options:**

**Option A: Remediate Immediately**
- Fix the issue (get missing KYC, correct rate, etc.)
- Notify customer if needed
- Document remediation
- Close exception

**Option B: Escalate for Approval**
- Exception requires management decision
- Escalate to Head of Operations or Compliance Head
- Obtain approval
- Proceed with approved action
- Document decision

**Option C: Restrict Account Temporarily**
- If regulatory issue cannot be immediately resolved
- Restrict transactions (hold pending resolution)
- Set deadline for remediation
- Reactivate once resolved

---

## Step 4: Customer Communication

### Notification Template

**Failed Transaction:**
```
Dear [Customer],

We attempted to process your [transfer/withdrawal] of £[amount] on [date], 
but it failed due to [reason].

Action taken: [Reprocessed/Refunded/Awaiting your action]

If you have questions, contact us at 0800-555-2265.

Regards,
FirstUK Bank Customer Service
```

**Disputed Transaction:**
```
Dear [Customer],

Thank you for reporting the disputed transaction of £[amount] on [date].

Investigation outcome: [Authorized/Unauthorized/Other]

Action: [Refund processed/Explanation provided/Escalated to merchant]

If you disagree with our findings, contact Financial Ombudsman Service.

Regards,
FirstUK Bank Customer Service
```

---

## Step 5: Quality Assurance

### Post-Resolution Review

**For Each Exception:**
- [ ] Investigation documented
- [ ] Resolution justified
- [ ] Customer notified
- [ ] If refund: Processed within SLA
- [ ] If escalated: Clear reason documented

**Monthly Exception Report:**
- Total exceptions received
- By category (failed, disputed, regulatory)
- By status (resolved, pending, escalated)
- Average resolution time
- Customer satisfaction rating
- Trends/patterns

---

## Escalation Rules

### When to Escalate

**Escalate Immediately If:**
- Financial amount >£5,000
- Regulatory implication
- Customer escalates to complaint
- Merchant/third-party involved
- Media/public concern

**Escalation Path:**
- Amount <£1,000: Operations Manager approval
- Amount £1,000–£5,000: Director approval
- Amount >£5,000: Executive approval
- Regulatory: Compliance Head approval

See [[escalation_matrix]] for detailed escalation rules

---

## Common Exceptions & Resolutions

### Failed Transfer — Insufficient Funds

```
Exception: Customer attempted £5,000 transfer, balance only £3,000
Investigation: Confirmed balance at transaction time
Resolution: Explain to customer, offer retry once funds available
SLA: 1 business day (immediate notification)
```

### Disputed Standing Order

```
Exception: Customer claims unauthorized standing order to "Unknown Recipient"
Investigation: Found legitimate standing order to Mortgage Company (customer's mortgage)
Resolution: Educate customer on standing order setup, explain visibility in online banking
SLA: 2 business days
```

### Duplicate Charge

```
Exception: Customer charged twice for £500 transfer
Investigation: Two identical transactions within 1 minute (system duplicate prevention failed)
Resolution: Refund second charge + interest + £50 goodwill compensation
SLA: 3 business days (refund within 10)
```

### Missing KYC Documentation

```
Exception: Account opened without identity verification (audit finding)
Investigation: KYC file missing; customer opened via online
Resolution: Email customer, request identity document immediately
SLA: 5 days (account restricted if doc not provided)
```

---

## Related Documents

- [[escalation_matrix]] — Escalation procedures
- [[policy_complaints]] — Complaint procedures
- [[business_ontology]] — Transaction entity
- [[systems]] — Account Management & Payment Systems

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
