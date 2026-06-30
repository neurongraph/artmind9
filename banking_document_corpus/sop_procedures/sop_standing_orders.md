# FirstUK Bank — Standing Order Processing SOP

## Metadata

| Field | Value |
|-------|-------|
| Document ID | STORD-SOP-001 |
| Version | 1.0 |
| Effective Date | 2026-01-15 |
| Review Date | 2027-01-15 |
| Owner | Head of Operations |
| Department | Operations, Retail Banking |
| Status | Active |
| Classification | Internal |
| Audience | Operations Staff, Branch Staff, Customer Service |
| Related Documents | [[sop_account_opening]], [[business_ontology]], [[systems]] |

---

## Purpose

Establish standardized procedures for setting up, maintaining, modifying, and canceling standing orders to ensure accurate, timely execution of customer-initiated recurring transfers.

---

## Scope

Applies to:
- All standing order creation requests
- All channels (online, branch, phone)
- Standing order modifications and cancellations
- Failed standing order handling

---

## Process Overview

```
Customer Requests Standing Order
    ↓
Collect Standing Order Details
    ↓
Validate Information
    ↓
Set Up in System (AMS/PPS)
    ↓
Confirm with Customer
    ↓
✅ Standing Order Active
    ↓
Execute on Schedule
    ↓
Monitor for Failures
```

---

## Step 1: Customer Request

### Channels for Request

**Online:** Self-service via online banking portal  
**Phone:** Call 0800-555-2265  
**Branch:** Speak with staff member  
**Mail:** Send request with account details

### Initial Information Collected

- Account number (source account)
- Payee name (recipient)
- Payee account number (for internal transfers) or sort code + account (for external)
- Amount (fixed, no variations)
- Frequency (weekly, monthly, quarterly, annual)
- Start date (when first payment should occur)
- End date (optional, when standing order should stop)

---

## Step 2: Validate Details

### Validation Checks

**Source Account:**
- ✅ Account exists and is active
- ✅ Account holder matches requestor
- ✅ Account has sufficient funds (for first payment)

**Payee Information:**
- ✅ Recipient account number valid (format check)
- ✅ Sort code valid (if external transfer)
- ✅ Recipient name provided and non-empty

**Amount & Frequency:**
- ✅ Amount > £0 and reasonable (< daily balance)
- ✅ Frequency valid (weekly, monthly, etc.)
- ✅ Start date is future date (not past)

**If Validation Fails:**
- Contact customer for correction
- Clarify requirements
- Collect correct information
- Re-validate

---

## Step 3: System Setup

### Standing Order Creation in AMS/PPS

**Fields Entered into System:**

| Field | Example | Notes |
|---|---|---|
| Standing Order ID | SO-00001234 | Auto-generated |
| Source Account | ACC-00001 | Customer's account |
| Payee Name | John Smith | Recipient name |
| Payee Account | 12345678 | Recipient's account |
| Payee Sort Code | 20-00-00 | Bank sort code |
| Amount | £500.00 | Fixed amount |
| Frequency | Monthly | Recurrence pattern |
| Start Date | 2026-02-01 | First execution date |
| End Date | None | Ongoing (no end date) |
| Status | Active | Standing order live |
| Created Date | 2026-01-15 | Creation date |
| Created By | Staff ID | Who set it up |

### Frequency Scheduling

**Monthly:** Executes on same calendar day each month  
**Weekly:** Executes every 7 days  
**Quarterly:** Executes every 3 months  
**Annual:** Executes once per year  

**Example:** £500 monthly on the 15th
- Jan 15: £500 transferred
- Feb 15: £500 transferred
- Mar 15: £500 transferred
- (continues indefinitely or until end date)

---

## Step 4: Confirm with Customer

### Confirmation Details Provided

**Email/SMS Confirmation Should Include:**
- Standing order ID (SO-00001234)
- Payee name and account number (masked: ****5678)
- Amount and frequency
- Start date
- Confirmation that standing order is active

**Customer Must Receive:**
- ✅ Email confirmation (preferred)
- ✅ SMS confirmation (if requested)
- ✅ Verbal confirmation (if by phone)
- ✅ Written receipt (if in branch)

---

## Step 5: Standing Order Execution

### Scheduled Processing

**Timing:**
- Standing order executes on scheduled date (e.g., 15th of month)
- Processing occurs early morning (before business hours)
- Funds transferred via FPS (Faster Payments Service)
- Recipient account receives funds within 1 hour

**Execution Process:**
1. System identifies standing orders due
2. Validates source account has sufficient funds
3. Initiates FPS transfer
4. Records transaction in ledger
5. Updates standing order status (completed)
6. Notifies customer (optional, if opted in)

**If Funds Unavailable:**
- Transfer fails (declined)
- Customer notified of failure
- Retry attempt made (usually once)
- Manual intervention if persistent failure

---

## Step 6: Modify Standing Order

### Changes Allowed

Customer can modify:
- ✅ **Amount:** Change payment amount (creates new SO, cancels old)
- ✅ **Frequency:** Change timing (weekly → monthly, etc.)
- ✅ **Payee:** Change recipient (creates new SO, cancels old)
- ✅ **End Date:** Set or change end date

### Modification Process

**Online Modification:**
1. Customer logs into online banking
2. Selects standing order to modify
3. Changes desired field(s)
4. Confirms modification
5. Receives confirmation

**Branch/Phone Modification:**
1. Customer calls or visits branch
2. Provides standing order ID and current details
3. Specifies changes requested
4. Staff member updates system
5. Sends confirmation to customer

**Note:** Full changes (amount, payee, frequency) may require cancellation of old SO and creation of new SO

---

## Step 7: Cancel Standing Order

### Cancellation Request

**How to Request:**
- Online: Self-service cancellation in portal
- Phone: Call 0800-555-2265
- Branch: Visit and request cancellation
- Mail: Send written cancellation request

**Information Needed:**
- Standing order ID (if known)
- Payee name
- Amount
- Account number

### Cancellation Process

**Steps:**
1. Customer requests cancellation
2. Staff verifies customer identity
3. Confirms standing order to cancel
4. Sets status to "Cancelled"
5. Effective date: Immediately (no notice period)
6. Next scheduled payment blocked
7. Confirmation sent to customer

**Timeline:** Cancellation effective immediately  
**No Fees:** Cancellation is free  
**No Notice Required:** Customer can cancel anytime with no notice

### Cancellation Confirmation

**Customer Receives:**
- Email confirmation of cancellation
- Standing order ID that was canceled
- Date of cancellation
- Confirmation that no further payments will be made

---

## Step 8: Failed Standing Orders

### Failure Scenarios

**Common Failure Reasons:**
- Insufficient funds in source account
- Invalid recipient account number
- Recipient account closed
- Payee bank system failure
- Duplicate prevention (if failed before)

### Failure Handling

**Step 1: Detect Failure**
- System logs failed standing order attempt
- Marks standing order status as "Failed"

**Step 2: Alert Customer**
- Email/SMS sent to customer
- Alert includes: Amount, recipient, failure reason
- Request for action (provide funds, update payee)

**Step 3: Retry Attempt**
- System retries once (usually within 24 hours)
- If retry successful: Standing order resumes

**Step 4: Escalation**
- If retry fails: Standing order suspended
- Staff review required
- Contact customer to resolve

**Step 5: Resolution**
- Customer provides corrected information (if needed)
- Staff updates standing order
- Standing order resumes or customer opts for cancellation

---

## Monitoring & Control

### Weekly Standing Order Review

**Operations Staff Reviews:**
- Total value of standing orders executed
- Number of failed standing orders
- Unusual patterns (large amounts, unusual recipients)
- Customer complaints related to standing orders

**Reconciliation:**
- Standing order transactions match customer expectations
- No duplicate payments
- All executed on scheduled dates

### Audit Trail

**System Records:**
- All standing orders (active and canceled)
- Creation date and staff who created
- All modifications and cancellations
- All execution records (successful and failed)
- Customer communications

**Retention:** Retained for 6 years (per POCA regulations)

---

## Quality Assurance

### Standing Order Checklist

**Before Activation:**
- [ ] Source account verified
- [ ] Payee information valid (account/sort code format correct)
- [ ] Amount reasonable and > £0
- [ ] Frequency valid
- [ ] Start date is future date
- [ ] Customer confirmation received

**Monthly Audit:**
- [ ] 10+ standing orders reviewed
- [ ] Correct execution on scheduled date
- [ ] Correct amount transferred
- [ ] Correct recipient received funds
- [ ] Customer satisfied (no complaints)

---

## Related Documents

- [[sop_account_opening]] — Account opening (standing order setup available)
- [[business_ontology]] — Standing Order entity definition
- [[systems]] — Payment Processing System (executes standing orders)

---

## Document Version History

| Version | Date | Change | Author |
|---------|------|--------|--------|
| 1.0 | 2026-01-15 | Initial SOP | Head of Operations |

---

## Sign-Off

**Approved by:**  
Head of Operations — **Date: 2026-01-15**  
Head of Retail Banking — **Date: 2026-01-15**

---
