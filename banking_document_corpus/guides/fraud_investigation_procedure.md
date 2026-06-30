# FirstUK Bank — Fraud Investigation Procedure

## Metadata

| Field | Value |
|-------|-------|
| Document ID | FRAUD-INVEST-SOP-001 |
| Version | 1.0 |
| Effective Date | 2026-01-15 |
| Review Date | 2027-01-15 |
| Owner | Head of Financial Crime |
| Department | Financial Crime, Risk, Legal |
| Status | Active |
| Classification | Confidential |
| Audience | Financial Crime Team, Risk, Compliance, Legal |
| Related Documents | [[policy_fraud]], [[incident_response_plan]], [[escalation_matrix]], [[sop_exception_handling]], [[policy_aml]] |

---

## Purpose

Establish standardized procedures for investigating suspected fraud cases, ensuring thorough documentation, appropriate escalation, and coordinated response with law enforcement.

---

## FRAUD INVESTIGATION FRAMEWORK

### Fraud Categories

**FirstUK Bank fraud typically falls into:**

| Category | Examples | Typical Loss |
|---|---|---|
| **Account Takeover** | Fraudster gains access to customer account, transfers funds | £500–£5,000 |
| **Unauthorized Transaction** | Customer claims transaction not authorized | £200–£10,000 |
| **Check Fraud** | Stolen/forged check deposited | £1,000–£50,000 |
| **Card Fraud** | Card number stolen, used for online/retail | £100–£5,000 |
| **Impersonation** | Fraudster opens account using false identity | £1,000–£20,000 |
| **Collusion (Internal)** | Employee facilitates fraud | £5,000–£500,000 |
| **AML/Sanctions Evasion** | Customer deliberately evades screening | £1,000–£1M+ |

---

## PART 1: FRAUD DETECTION & INTAKE

### Fraud Detection Sources

**How Fraud Is Discovered:**

1. **Automated Detection** (60% of fraud found)
   - Fraud Detection Engine (FDE) flags transaction (97%+ detection rate)
   - System alert triggers investigation
   - Example: Multiple rapid international transfers

2. **Customer Report** (25% of fraud found)
   - Customer calls: "I didn't authorize this transaction"
   - Customer notices unauthorized account activity
   - Disputed charge on statement

3. **Internal Detection** (10% of fraud found)
   - Staff member notices suspicious activity
   - Back-office reconciliation identifies discrepancy
   - Teller observes unusual transaction request

4. **Law Enforcement** (5% of fraud found)
   - Police report of compromised account
   - Federal agency investigation (FBI, OFAC, etc.)
   - International law enforcement inquiry

---

### Intake Process

**Upon Fraud Allegation:**

**Step 1: Initial Report**

```
Fraud Report Form:
- Fraud ID: FRAUD-[DATE]-[001-999] (e.g., FRAUD-2026-0115-001)
- Report Date: [Date/Time]
- Reporter: [Name, Department, Phone]
- Reporter Category: [Customer / Staff / System / Law Enforcement]

Alleged Fraud Details:
- Customer Name & Account: [ACC-XXXXX]
- Transaction(s) in Question: [List with dates, amounts]
- Alleged Fraudster Identity (if known): [Description]
- Fraud Type: [Account Takeover / Unauthorized Transaction / Other]
- Alleged Loss Amount: [£Amount]
- Report Urgency: [Routine / Urgent / Critical]
```

**Step 2: Initial Triage**

```
Is This Urgent?
☐ YES → Immediate action required (within 1 hour)
  - Account takeover in progress
  - Large amount in immediate danger
  - Law enforcement/critical incident
  
☐ NO → Standard investigation (within 24 hours)
  - Disputed transaction already posted
  - Customer noticed but no immediate threat
  - Routine investigation
```

**Step 3: Account Security Actions** (if urgent)

```
IMMEDIATE ACTIONS (First 5 Minutes):
- [ ] Freeze customer account (block transactions)
- [ ] Block customer's debit/credit cards (prevent further fraud)
- [ ] Reset customer's online banking password
- [ ] Log customer out of all sessions
- [ ] Document actions taken & time
- [ ] Notify customer (by phone if possible)
- [ ] Escalate to Financial Crime Head if >£5,000
```

---

## PART 2: INVESTIGATION PROCEDURES

### Step 1: Evidence Preservation

**Preserve All Evidence:**

```
Required:
- [ ] Original transaction records (screenshots, system data)
- [ ] Account statements (30 days before fraud date)
- [ ] All communications (customer emails, phone notes)
- [ ] System logs (login history, IP addresses, timestamps)
- [ ] Device information (if known: phone, computer used)
- [ ] Geo-location data (where transaction originated)
- [ ] Any written statements (customer, staff)

Storage:
- All evidence secured in locked file (physical) or encrypted (digital)
- Separate from normal account files
- Access limited to investigation team
- Not deleted, edited, or altered
- Retention: Minimum 7 years
```

---

### Step 2: Transaction Analysis

**For Each Disputed/Flagged Transaction:**

```
Transaction Details:
- Transaction ID: [ID]
- Date/Time: [Exact timestamp]
- Amount: [£Amount]
- Type: [Transfer / Card / Check / Other]
- Merchant/Recipient: [Name, ID]
- Merchant Category: [Retail / Airline / Casino / Other]
- Device Used: [Online / Mobile App / ATM / Branch / Phone]
- IP Address (if digital): [IP]
- Geo-location: [City, Country]

Transaction Legitimacy Assessment:
- Is amount typical for customer? ☐ Yes ☐ No ☐ Unusual
- Is merchant typical for customer? ☐ Yes ☐ No ☐ Unusual
- Is timing consistent with known patterns? ☐ Yes ☐ No
- Is device new/unusual? ☐ Yes ☐ No
- Is geo-location different from normal? ☐ Yes ☐ No ☐ Impossible

Red Flags:
- ☐ Multiple transactions in short time (velocity)
- ☐ Transactions at 3 AM (unusual time)
- ☐ High-risk merchants (casinos, money transfer)
- ☐ Geo-impossibility (2 transactions on opposite sides of world within minutes)
- ☐ Change from habitual pattern (customer never transfers to this recipient)
```

---

### Step 3: Customer Interview

**Interview Procedure:**

**Timing:** Within 24 hours of report (unless law enforcement requests delay)

**Scope:** Understanding customer's legitimate activities vs. fraudulent

**Questions to Ask:**

```
1. AUTHENTICATION & AUTHORIZATION
   "Did you authorize this transaction of £[amount] on [date]?"
   "What is your relationship with [recipient]?"
   "Would you ever send money to a stranger/unknown entity?"

2. ACCOUNT ACCESS
   "Where are your cards normally kept?"
   "When did you last use your card in person?"
   "When did you last change your online banking password?"
   "Do you share your PIN/password with anyone?"
   
3. DEVICE SECURITY
   "Do you use the same device (phone/computer) for online banking?"
   "Have you downloaded unusual apps recently?"
   "Has your device been lost or stolen?"
   
4. COMMUNICATION
   "Have you received suspicious emails requesting information?"
   "Have you received calls asking for account details?"
   "Did anyone contact you before this transaction?"
   
5. MERCHANT/RECIPIENT
   "Do you recognize [merchant name]?"
   "Have you done business with them before?"
   "Did you initiate contact, or did they contact you?"

6. TIMELINE RECONSTRUCTION
   "Walk me through your day on [date]."
   "Where were you when this transaction occurred?"
   "When did you first notice the unauthorized transaction?"
```

**Documentation:**

- [ ] Interview conducted: [Date/Time]
- [ ] Conducted by: [Name, Title]
- [ ] Method: [Phone / In-person / Email]
- [ ] Customer statement taken (audio recorded if possible)
- [ ] Customer confirms facts or disputes

---

### Step 4: Evidence Evaluation

**Assess Fraud Likelihood:**

**For Each Piece of Evidence, Score:**

| Evidence | Value | Assessment |
|---|---|---|
| **Customer Disputes Transaction** | 1–3 | 1=Weak claim, 3=Strong claim (backed by facts) |
| **Geo-Impossibility** | 0–2 | 2=Confirmed impossible, 0=Possible |
| **Device Mismatch** | 0–2 | 2=New device, 0=Known device |
| **Merchant Risk** | 0–2 | 2=High-risk merchant, 0=Legitimate |
| **Velocity** | 0–2 | 2=Multiple rapid transactions, 0=Single |
| **Customer History** | 0–2 | 2=Customer never does this, 0=Typical |

**Fraud Score = Sum of Evidence Points (0–12)**

| Score | Assessment | Action |
|---|---|---|
| **10–12** | Fraud Confirmed | Refund + Investigation |
| **7–9** | Fraud Likely | Refund + Investigation |
| **4–6** | Fraud Possible | Investigate further or refund pending |
| **1–3** | Fraud Unlikely | Likely authorized or error; educate customer |
| **0** | Fraud Not Likely | Deny fraud claim; educate customer |

---

### Step 5: Merchant/Third-Party Investigation

**If Fraud Involves External Merchant/Recipient:**

**For Card Transactions:**
- Contact merchant for transaction proof
- Request proof customer was present (if card-present)
- Request merchant records (receipt, camera footage)
- Review merchant chargeback history (are they repeat offender?)

**For Transfers to External Account:**
- Identify recipient bank
- Request transaction details (recipient name matches account?)
- Track recipient account (is it in sanctions list? Multiple fraud reports?)
- Identify if funds already withdrawn

**Timeline:**
- If funds still in recipient account: Attempt to recover (urgent)
- If funds withdrawn: Trace withdrawal location (law enforcement)

---

### Step 6: Forensic Investigation (If Serious/Complex)

**For High-Loss Fraud or Suspected Cybercrime:**

```
Forensic Analysis May Include:
- Email header analysis (trace phishing emails)
- Device forensics (if customer device involved)
- Network forensics (IP logs, ISP subpoena)
- Financial forensics (trace money flow)
- Database queries (identify linked frauds)
```

**Forensic Investigator Engagement:**
- Request made to Head of Financial Crime
- Case file transferred to external/internal forensics team
- Estimated timeline: 2–4 weeks
- Cost: £5k–£20k depending on complexity

**Findings May Reveal:**
- Identity of fraudster (if traceable)
- Fraud pattern (linked to other victims)
- Entry point (how fraud occurred)
- Recommendations for prevention

---

## PART 3: DECISION & RESOLUTION

### Step 1: Investigation Conclusion

**After All Evidence Gathered, Determine:**

```
DETERMINATION:
☐ CONFIRMED FRAUD — Fraudster unauthorized transaction
☐ DISPUTED TRANSACTION — Ambiguous, but balance of evidence favors customer
☐ AUTHORIZED — Customer authorized, now regrets
☐ CUSTOMER ERROR — System worked correctly; customer misunderstood
☐ INCOMPLETE — Insufficient evidence; close with follow-up conditions
```

---

### Step 2: Resolution by Fraud Type

**IF CONFIRMED FRAUD:**

```
Refund Process:
1. [ ] Issue full refund to customer account
2. [ ] Refund includes interest (daily rate × days delayed)
3. [ ] Timestamp refund decision
4. [ ] Process within 10 business days (Direct Debit Guarantee)
5. [ ] Notify customer in writing

Refund Example:
Original Fraudulent Transaction: £1,000 (posted Jan 5)
Refund Decision Date: Jan 7 (2 days later)
Interest Accrued: £1,000 × 0.05% annual rate ÷ 365 days × 2 days = £0.27
Total Refund: £1,000.27

Refund Posted: Jan 14 (within 10 days)
```

**Prevention Actions:**

```
To Prevent Similar Fraud:
1. [ ] Issue new card (old compromised)
2. [ ] Reset online banking password
3. [ ] Enable 2FA if not already active
4. [ ] Flag account for enhanced monitoring
5. [ ] Review standing orders / direct debits (may be compromised)
6. [ ] Offer identity theft protection (if account takeover)
7. [ ] Educate customer on how fraud occurred
```

---

**IF DISPUTED TRANSACTION:**

```
If Balance of Evidence Favors Customer:
- Issue refund (same process as confirmed fraud)
- Treat as potential fraud for prevention

If Balance of Evidence Inconclusive:
- Offer partial refund (e.g., 50%)
- Request additional verification
- Follow up in 30 days
```

---

**IF AUTHORIZED:**

```
Customer Authorized But Regrets:
- Explain transaction was authorized by customer
- No refund (customer dispute with merchant, not bank fraud)
- Offer chargeback process (if applicable)
- Educate on how to prevent future regrettable purchases
- Document customer education
```

---

**IF CUSTOMER ERROR:**

```
System Worked Correctly:
- No fraud occurred
- Customer misunderstood something
- Explain what happened
- Educate customer (e.g., "Direct Debit Guarantee protection", "Standing Order", etc.)
- No refund unless policy violation found
- Document in customer file (pattern of similar disputes?)
```

---

### Step 3: Fraud Classification & Reporting

**Classify Fraud for Reporting:**

```
Fraud Classification:
- Type: [Account Takeover / Card Fraud / Check Fraud / Impersonation / Other]
- Amount: [£Amount]
- Confirmed: [Yes / No]
- Recovered: [£Amount recovered, if any]
- Status: [Resolved / Under Investigation / Referred to Police]
```

**Reporting Requirements:**

**Financial Crime Report (Internal):**
- Submitted monthly to Head of Financial Crime
- Summary of fraud cases by type
- Trends (increasing/decreasing?)
- Prevention effectiveness

**Regulatory Reporting (If Required):**

**Suspicious Activity Report (SAR):**
- File if fraud suspected involving money laundering/sanctions evasion
- File with FCA within 30 days
- Example: Customer transfers funds to sanctions country

**Data Breach Report (If Applicable):**
- File if customer data compromised
- Notify FCA within 72 hours
- Notify customer immediately

---

## PART 4: LAW ENFORCEMENT COORDINATION

### When to Report to Police

**Mandatory Police Report:**
- Confirmed fraud >£10,000
- Serial fraud (multiple victims, pattern)
- Organized fraud ring
- Impersonation/identity theft
- Check fraud (coordinated with bank systems team)
- Customer requests police report

**Optional Police Report:**
- Fraud >£5,000 (encouraging reports)
- Repeated fraudster (pattern recognition)
- Suspected insider fraud (always report)

### Police Report Process

**Step 1: Internal Notification**
- [ ] Notify Head of Financial Crime
- [ ] Notify Head of Legal
- [ ] Document decision to report

**Step 2: File Police Report**
- [ ] Contact local police (non-emergency: 101 in UK)
- [ ] Provide case details, evidence
- [ ] Obtain police reference number
- [ ] Document in case file

**Step 3: Evidence Management**
- [ ] Preserve evidence for police investigation
- [ ] Don't disturb suspected fraudster's account (police may monitor)
- [ ] Coordinate with police on customer communication
- [ ] Provide police with requested documents/data

**Step 4: Follow-Up**
- [ ] Periodically contact police for case status
- [ ] Provide additional evidence if requested
- [ ] Document all communications

---

## PART 5: CASE DOCUMENTATION & CLOSURE

### Case File Contents

**Complete Fraud Investigation Case File Must Contain:**

```
☐ Original fraud report (intake form)
☐ Customer interview notes/recording
☐ Transaction documentation (screenshots, statements)
☐ Evidence collected (communications, system logs)
☐ Analysis & findings
☐ Investigation conclusion & determination
☐ Refund decision (if applicable)
☐ Prevention actions taken
☐ Police report reference (if applicable)
☐ Manager/Head of Crime sign-off
☐ Date case closed
☐ Timeline of investigation (from report to closure)
```

### Case Closure

**Investigation Complete When:**

```
1. All evidence gathered and analyzed
2. Determination made (fraud/not fraud/inconclusive)
3. Customer refunded (if applicable)
4. Prevention actions implemented
5. Police report filed (if applicable)
6. Case documented
7. Manager approval obtained
```

**Closure Notification to Customer:**

```
Sample Letter:

Dear [Customer Name],

We have completed our investigation into the disputed transaction 
of £[amount] on [date].

Finding: Unauthorized transaction (FRAUD CONFIRMED)

Action: Refund of £[amount] issued to your account on [date]

Please allow 1–2 business days for the refund to appear.

Prevention: We have:
- Issued new card (old one cancelled)
- Reset your online banking password
- Enabled extra security features
- Reported to police (reference: [number])

What You Should Do:
- Review your recent statements for other unauthorized transactions
- Update passwords on all important accounts (email, social media, etc.)
- Consider identity theft protection service
- Monitor your credit report

Questions? Contact us: 0800-555-2265

Best regards,
FirstUK Bank Financial Crime Team
```

---

## PART 6: FRAUD PREVENTION MEASURES

### Prevention Actions by Fraud Type

**Account Takeover Prevention:**
- Enforce password complexity requirements
- Implement 2FA on all accounts
- Monitor for login anomalies
- Educate customers on phishing

**Card Fraud Prevention:**
- Issue chip/contactless cards only
- Enable card alerts (SMS for transactions >£X)
- Implement velocity checks (max transactions per day)
- Require customer authentication for high-value transactions

**Impersonation Prevention:**
- Enhanced KYC verification
- Document original ID verification
- Monitor for duplicate IDs
- Flag suspicious patterns

**Check Fraud Prevention:**
- Implement positive pay (customer specifies issued checks)
- Require additional verification for large checks
- Monitor check clearing times (unusual delays = red flag)

---

## PART 7: COMMON MISTAKES (Don't Make These!)

❌ **Mistake 1:** Process refund without thorough investigation  
✅ **Correct:** Complete investigation; only refund if confirmed/likely fraud

❌ **Mistake 2:** Delete fraud case file after closure  
✅ **Correct:** Retain files for 7 years minimum (supports audit trail)

❌ **Mistake 3:** Report customer as fraudster before confirming  
✅ **Correct:** Keep investigation confidential until confirmed

❌ **Mistake 4:** Delay notifying customer of fraud  
✅ **Correct:** Notify within 24 hours so customer can take protective action

❌ **Mistake 5:** Fail to implement prevention after fraud occurs  
✅ **Correct:** Always implement prevention (new card, password reset, 2FA, etc.)

---

## Escalation & Authorities

| Fraud Amount | Investigation Level | Approval Authority | Timeline |
|---|---|---|---|
| **<£500** | Routine | CSR | 2–3 days |
| **£500–£5,000** | Standard | Manager | 3–5 days |
| **£5,000–£50,000** | Enhanced | Risk Manager | 5–7 days |
| **>£50,000** | Complex | Head of Crime + Legal | 7–14 days |
| **Serial/Pattern** | Specialized | Head of Crime + Police | 10+ days |

---

## Related Documents

- [[policy_fraud]] — Fraud prevention policy
- [[incident_response_plan]] — Incident response procedures
- [[escalation_matrix]] — Decision authority levels
- [[sop_exception_handling]] — Exception handling (overlaps with fraud)
- [[policy_aml]] — AML/sanctions fraud detection

---

## Document Version History

| Version | Date | Change | Author |
|---------|------|--------|--------|
| 1.0 | 2026-01-15 | Initial fraud investigation SOP | Head of Financial Crime |

---

## Sign-Off

**Approved by:**  
Head of Financial Crime — **Date: 2026-01-15**  
Head of Legal — **Date: 2026-01-15**  
Chief Risk Officer — **Date: 2026-01-15**

---

**Confidential — Financial Crime Distribution Only**  
**Investigation procedures are sensitive and should not be disclosed to non-authorized parties.**
