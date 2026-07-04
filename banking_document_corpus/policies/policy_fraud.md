# FirstUK Bank — Fraud Prevention Policy

## Metadata

| Field | Value |
|-------|-------|
| Document ID | FRAUD-POL-007 |
| Version | 1.1 |
| Effective Date | 2026-01-15 |
| Review Date | 2027-01-15 |
| Owner | Head of Financial Crime |
| Department | Financial Crime |
| Status | Active |
| Classification | Internal |
| Audience | All Staff, Financial Crime, Risk, Retail Banking |
| Regulatory Reference | FCA COBS, Payment Services Regulations 2017 |
| Related Documents | [[policy_aml]], [[departments]], [[systems]] |

---

## Executive Summary

FirstUK Bank maintains a Fraud Prevention Policy to detect, prevent, and respond to fraudulent transactions and account takeover. The policy establishes procedures for fraud detection, customer reimbursement, and investigation.

---

## Purpose & Scope

### Purpose

To establish fraud prevention procedures that:
- Detect unauthorized transactions (fraud)
- Block fraudulent activity before settlement
- Reimburse customers fairly for fraud losses
- Investigate fraud cases
- Prevent repeat fraud by same perpetrator

### Scope

Applies to:
- All customer accounts (savings, current, mortgages)
- All transaction types (deposits, withdrawals, transfers, card payments)
- All fraud scenarios (card fraud, account takeover, check fraud, etc.)
- All channels (branch, online, mobile, phone, ATM)

---

## Policy Statement

**FirstUK Bank protects customers from fraud through detection systems, real-time transaction monitoring, and fair liability allocation.**

---

## Fraud Categories

### 1. Authorized Fraud (Customer Liability)

**Scenario:** Customer authorizes transaction but later claims fraud

**Characteristics:**
- Customer knew of transaction
- Customer provided details to criminal
- Customer lacks authorization
- Negligence by customer

**Examples:**
- Customer calls "bank" (actually criminal) and authorizes transfer
- Customer gives card details to stranger claiming to be support
- Customer wire transfers to criminal "investment opportunity"

**Customer Liability:**
- Customer liable for loss IF customer negligent
- Exception: Fraud by staff member or unauthorized card use

---

### 2. Unauthorized Fraud (Bank Liable)

**Scenario:** Criminal uses account without customer authorization

**Characteristics:**
- Customer did not authorize transaction
- Criminal obtained access (stolen password, card, etc.)
- Customer's liability: £0 (if reported within 24 hours)

**Examples:**
- Card stolen, used by criminal
- Password compromised, unauthorized online transfer
- Account takeover via phishing
- Card cloning at ATM

**Bank Liability:**
- FirstUK Bank reimburses 100% of unauthorized loss
- Reimbursement within 10 business days
- No customer liability (unless gross negligence)

---

### 3. Card Fraud

**Card Not Present (CNP) Fraud:**
- Online purchase, card details stolen
- E.g., website hacking, data breach
- Bank liability depends on authentication strength

**Card Present Fraud:**
- Physical card used by thief
- E.g., card stolen from customer
- Bank liable (unless customer negligent)

**Card Cloning:**
- Card details copied from legitimate card
- Counterfeit card created
- Bank liable for losses post-report

---

### 4. Account Takeover

**Definition:** Criminal gains access to legitimate account (password, security questions)

**Attack Vectors:**
- Phishing email (fake login page)
- Password breach (from third-party data breach)
- Social engineering (customer support impersonation)
- SIM swap (attacker intercepts SMS 2FA)
- Malware on customer's device

**Bank Actions:**
- Immediate account freeze
- Password reset forced
- Credential review (security questions)
- MFA re-verification
- Full reimbursement of losses

---

## Fraud Detection Framework

### Detection Methods

**1. Real-Time Transaction Monitoring (Fraud Detection Engine)**

**See:** [[systems]] Fraud Detection Engine (FDE-001)

**Rules Monitored:**
- Large transaction amount (>£5,000)
- Velocity checks (>5 transactions in 1 hour)
- Geographic anomaly (transaction in different country within 2 hours)
- New beneficiary with large transfer
- Unusual merchant/category
- Cash withdrawal followed by immediate large transfer
- Multiple failed authentication attempts

**Action:** Alert generated, transaction placed on hold (up to 5 days)

**2. Manual Review**

**Triggers for Manual Investigation:**
- Customer reports suspected fraud
- Unusual account activity
- Pattern changes (normal behavior deviation)
- High-value transaction
- Regulatory alert (sanctions, AML)

**Review Process:**
- Financial Crime team analyzes activity
- Customer contacted for explanation (if safe)
- Transaction approved or blocked based on risk

**3. Fraud Pattern Analysis**

**Advanced Analytics:**
- Machine learning model learns normal customer behavior
- Anomalies flagged for review
- Recurring fraud patterns identified
- Fraud perpetrators tracked across customers

**Feedback Loop:**
- Confirmed fraud updates model
- False positives refined
- Model continuously improves

---

## Customer Fraud Reporting

### Reporting Procedure

**1. Customer Reports Suspected Fraud**
- Via: Phone (0800-555-2265), email, branch, online
- Capture: Full details, timeline, amounts

**2. Immediate Actions**
- Acknowledge report
- Freeze account (if account takeover)
- Block affected card (if card fraud)
- Escalate to Financial Crime team
- Provide temporary access (alternate card/account)

**3. Investigation (24–48 hours)**
- Review transaction details
- Assess fraud likelihood
- Check payment status
- Determine liability
- Document findings

**4. Reimbursement Decision**
- **If Fraud Confirmed:** Full reimbursement within 10 days
- **If Not Fraud:** Explain reasoning, educate customer
- **If Disputed:** Escalate to Head of Financial Crime + follow-up

---

## Liability & Reimbursement

### Customer Liability

**Zero Liability (Bank Pays All):**
- Unauthorized card use (if reported within 24 hours)
- Account takeover (not customer's fault)
- Stolen check/account details used
- Scam (customer tricked by fraudster impersonating bank)

**Reduced Liability (Customer Pays Part):**
- Gross negligence (e.g., writing password on card)
- Customer knew fraudster
- Delayed reporting (>24 hours after discovery)

**Full Customer Liability (Customer Pays All):**
- Authorized transaction (customer authorized but later regrets)
- Negligence (wrote password down, shared card)
- Breach of security (unauthorized person given access)

### Reimbursement Timeline

| Fraud Type | Liable Party | Reimbursement Timeline |
|---|---|---|
| Unauthorized card | Bank | 10 business days |
| Account takeover | Bank | 10 business days |
| Fraudulent transfer | Bank (if FPS not settled) | 10 business days |
| Scam (tricked customer) | Bank | 10 business days |
| Customer negligence | Customer | No reimbursement |

---

## Fraud Investigation

### Investigation Process

**Triggers:**
- Confirmed unauthorized fraud (>£1,000)
- Multiple fraud incidents (same customer/merchant)
- Fraud pattern identified (systemic issue)
- Law enforcement referral (police report filed)

**Investigation Team:**
- Financial Crime staff (lead investigator)
- Technology team (system analysis, logs)
- Legal (if law enforcement involved)
- External forensics (if data breach suspected)

**Investigation Elements:**
- Timeline of fraudulent transactions
- Perpetrator identification (if possible)
- Attack method analysis
- Loss quantification
- Recommendations for prevention

**Evidence Preservation:**
- System logs retained
- Transaction records protected
- Customer communications archived
- Cooperation with law enforcement

---

## Prevention & Controls

### System Controls (Fraud Detection Engine)

**Real-Time Blocking:**
- High-risk transactions auto-blocked
- Risky transactions require OTP verification
- Medium-risk transactions held for manual review

**Authentication Strengthening:**
- Multi-factor authentication (MFA) for online banking
- Biometric authentication for mobile
- SMS/email confirmation for transfers

**Transaction Limits:**
- Daily withdrawal limit (per card): £500 ATM
- Daily transfer limit: Configurable by customer (default £50k)
- New beneficiary limits: £1k for first transfer

### Customer Education

**Customer Awareness:**
- Security tips in welcome pack
- Phishing email examples in communications
- "Never give password to anyone" messages
- Advice: "If bank calls, call bank back using known number"

**Reporting Encouragement:**
- Clear fraud reporting channel (0800-555-2265)
- Online fraud form available
- Branch staff trained on fraud questions
- Rapid response to fraud reports

### Staff Controls

**Staff Training:**
- Annual fraud awareness (all staff)
- Fraud scenario training
- Red flags identification
- Proper documentation

**Segregation of Duties:**
- Fraud detection separate from transaction processing
- Investigation separate from approval
- No single person can block and approve transaction

---

## Law Enforcement Coordination

### Police Involvement

**When Customer Files Police Report:**
- FirstUK Bank cooperates fully
- Provides transaction records to police
- Preserves evidence (logs, screenshots)
- Follows police investigation protocol
- May delay reimbursement pending investigation (if indicated)

### Information Sharing

**FCA/Regulatory Notification:**
- Large fraud losses reported to FCA
- Fraud patterns shared with industry
- Suspicious activity referral to NCA (if ML suspected)

---

## Fraud Statistics & Monitoring

### Metrics Tracked

| Metric | Target | Actual (2025) |
|---|---|---|
| Fraud detection rate | 97%+ | 97.2% |
| False positive rate | <2% | 1.8% |
| Average fraud loss per incident | <£5k | £3.2k |
| Annual fraud loss % of revenue | <0.5% | 0.35% |
| Reimbursement timeliness | 100% within 10 days | 99% |

### Reporting

**Monthly Report:**
- Fraud incidents reported
- Loss amount
- Detection method
- Trend analysis

**Quarterly Report to Risk Committee:**
- Fraud risk assessment
- Top fraud types/patterns
- Process improvements implemented
- Fraud prevention effectiveness

---

## Industry Collaboration

### Fraud Sharing

**Industry Fraud Information:**
- Customer Information System (CIS) — Fraud information shared among banks
- Fraud patterns shared with industry peers
- Card fraud networks (if applicable)

**Early Warning System:**
- NewPay alerts (payment scams emerging)
- Telecommunications fraud alerts
- Emerging fraud trends

---

## Regulatory Compliance

**Regulations:**
- FCA COBS (fraud prevention, fair liability)
- Payment Services Regulations (transaction authorization, dispute resolution)
- Chargeback rights (credit cards, if applicable)

**Compliance Requirements:**
- Fair liability allocation (not favoring bank)
- Prompt investigation (within 30 days for disputes)
- Reimbursement without delay (if fraud confirmed)
- Evidence-based decisions (documented reasoning)

---

## Staff Responsibilities

### All Staff

- Report suspected fraud immediately
- Educate customers about fraud prevention
- Verify suspicious customer contacts
- Follow fraud procedures
- Document suspicions

### Customer Service

- Acknowledge fraud reports promptly
- Initiate investigation
- Update customers on progress
- Process reimbursements
- Provide fraud prevention advice

### Financial Crime Team

- Investigate fraud claims
- Determine liability
- Oversee reimbursement
- Identify fraud patterns
- Recommend prevention measures

### Compliance

- Monitor fraud trends
- Report statistics to regulators
- Audit fraud processes
- Ensure compliance with regulations

---

## Training & Awareness

**Mandatory Annual Training:**
- All staff: 1-hour fraud awareness training
- Financial Crime staff: Quarterly specialist training
- Customer Service: 2 hours fraud investigation and customer communication
- Management: Fraud governance and escalation

---

## Audit & Monitoring

**Internal Audit:**
- Annual fraud control testing
- File sampling (fraud investigations)
- Reimbursement accuracy and timeliness
- Evidence documentation

**Regulatory Audit:**
- FCA periodic fraud prevention reviews
- Compliance with payment services regulations

---

## Related Documents

- [[policy_aml]] — Anti-Money Laundering (overlaps with fraud)
- [[systems]] — Fraud Detection Engine
- [[departments]] — Financial Crime Department
- Fraud Investigation Procedure (detailed steps)

---

## Policy Review & Updates

**Review Frequency:** Annual (or upon emerging fraud type)  
**Last Review:** 2026-01-15  
**Next Review:** 2027-01-15  

---

## Sign-Off

**Approved by:**  
Head of Financial Crime — **Date: 2026-01-15**  
Chief Risk Officer — **Date: 2026-01-15**  
Chief Executive Officer — **Date: 2026-01-15**

---
