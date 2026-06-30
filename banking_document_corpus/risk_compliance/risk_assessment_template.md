# FirstUK Bank — Risk Assessment Template & Guidance

## Metadata

| Field | Value |
|-------|-------|
| Document ID | RISK-ASSESS-TEMPLATE-001 |
| Version | 1.0 |
| Effective Date | 2026-01-15 |
| Review Date | 2027-01-15 |
| Owner | Head of Risk |
| Department | Risk, Compliance |
| Status | Active |
| Classification | Internal |
| Audience | Risk Team, Department Heads, Project Managers |
| Related Documents | [[risk_appetite_statement_2026]], [[policy_operational_risk]], [[escalation_matrix]], [[incident_response_plan]] |

---

## Purpose

Provide standardized template and guidance for assessing risks across FirstUK Bank operations, ensuring consistent risk evaluation and decision-making.

---

## PART 1: RISK ASSESSMENT TEMPLATE

### Header Section

```
RISK ASSESSMENT FORM
Date Prepared: [DD/MM/YYYY]
Prepared By: [Name, Title, Department]
Reviewed By: [Name, Title]
Assessment Period: [Date Range]
Risk ID: RISK-[YYYY]-[001-999]

Process/Project Name: [e.g., "Account Opening Process" or "New Mortgage Product Launch"]
Business Unit: [e.g., "Retail Banking" or "Product Management"]
Process Owner: [Name, Title]
```

---

### Risk Identification Section

**Step 1: Describe the Process/Project**

```
Process/Project Description:
[2-3 sentence overview of what is being assessed]

Example:
"Account opening process via online channel. Customers complete application, 
provide KYC documents, undergo AML screening, and receive account within 2 business days. 
Process involves 4 systems (IBP, AMS, FDE, DW) and 2 staff departments (Customer Service, Compliance)."

Scope Boundaries:
- Included: [What's in scope]
- Excluded: [What's out of scope]

Key Stakeholders:
- Primary: [Who owns this]
- Secondary: [Who is impacted]
```

**Step 2: Identify Risk Factors**

For each process step, identify potential risks:

```
Process Step | Risk Factor | Description | Category
---|---|---|---
Application Form | Data Entry Error | Incorrect customer name captured | Operational
KYC Verification | Fraud | Forged document submitted | Compliance
AML Screening | False Positive | Legitimate customer matches sanctions list | Compliance
Account Creation | System Failure | AMS unavailable, account not created | Technology
Statement Delivery | Mail Loss | Physical statement lost in post | Operational
```

---

### Risk Analysis Section

**For Each Identified Risk, Complete:**

```
RISK #1: [Risk Name]

1. RISK DESCRIPTION
   What could go wrong? 
   [2-3 sentences explaining the risk]

2. RISK CATEGORY
   ☐ Credit Risk (loan defaults, credit losses)
   ☐ Operational Risk (process failures, errors, fraud)
   ☐ Compliance Risk (regulatory violation, policy breach)
   ☐ Technology Risk (system failure, cyber attack)
   ☐ Fraud Risk (theft, money laundering, impersonation)
   ☐ Reputational Risk (brand damage, customer loss)
   ☐ Liquidity Risk (cash flow constraints)
   ☐ Market Risk (interest rate, currency)

3. RISK SOURCE
   What causes this risk?
   ☐ Internal Process (how we do things)
   ☐ People (staff behavior, competence)
   ☐ Systems (technology failure)
   ☐ External (regulators, competitors, fraud)
   ☐ Compliance (regulatory change, policy)

4. LIKELIHOOD ASSESSMENT
   How likely is this risk to occur?
   
   Likelihood Score: [1-5]
   1 = Remote (unlikely to occur in foreseeable future)
   2 = Low (may occur in 3+ years)
   3 = Medium (may occur in 1–3 years)
   4 = High (likely to occur in <1 year)
   5 = Very High (could occur multiple times per year)
   
   Supporting Evidence:
   - [Historical incidents: How many times has this occurred?]
   - [Industry benchmarks: How common is this risk?]
   - [Trend: Is this getting more/less likely?]
   - [Control effectiveness: Are our controls preventing this?]

5. IMPACT ASSESSMENT
   If this risk occurs, what is the financial and non-financial impact?
   
   Impact Dimension | Level | Amount/Description
   ---|---|---
   **Financial** | [Low/Medium/High] | £[Amount] or % of revenue
   **Regulatory** | [Low/Medium/High] | [Fines, restrictions, license loss]
   **Customer** | [Low/Medium/High] | [Number affected, satisfaction loss]
   **Operational** | [Low/Medium/High] | [Downtime, process disruption]
   **Reputational** | [Low/Medium/High] | [Media coverage, brand damage]
   
   Impact Score: [1-5]
   1 = Negligible (minor inconvenience)
   2 = Minor (<£100k loss or 10 customers)
   3 = Moderate (£100k–£500k loss or 50 customers)
   4 = Major (£500k–£1M loss or 100 customers)
   5 = Critical (>£1M loss or 1,000+ customers, regulatory action)

6. RISK SCORING
   
   Risk Score = Likelihood × Impact
   [1-5] × [1-5] = [1-25]
   
   Risk Level:
   - 1–5 = Low Risk (monitor, no action)
   - 6–12 = Medium Risk (manage, control improvements)
   - 13–18 = High Risk (active management, escalate)
   - 19–25 = Critical Risk (urgent action, senior management)
   
   Calculated Risk Score: [Value]
   Risk Level: [Low/Medium/High/Critical]

7. EXISTING CONTROLS
   What controls currently exist to mitigate this risk?
   
   Control | Owner | Effectiveness | Evidence
   ---|---|---|---
   [e.g., "Document verification procedure"] | [Risk/Operations] | [High/Medium/Low] | [Testing results]
   
   Effectiveness Assessment:
   ☐ Strong (control prevents/detects risk effectively)
   ☐ Adequate (control provides reasonable assurance)
   ☐ Weak (control doesn't reliably prevent/detect)
   ☐ None (no control exists)

8. RESIDUAL RISK
   After existing controls, what is the remaining risk?
   
   Residual Risk = Risk Score × (1 – Control Effectiveness%)
   
   Example:
   - Original Risk Score: 20 (Critical)
   - Control Effectiveness: 75%
   - Residual Risk: 20 × (1 – 0.75) = 5 (Low)
   
   Calculated Residual Risk: [Value]
   Residual Risk Level: [Low/Medium/High/Critical]

9. RISK APPETITE TOLERANCE
   Does this residual risk align with FirstUK Bank's risk appetite?
   
   Per [[risk_appetite_statement_2026]]:
   - Credit Risk: <0.5% loss ratio
   - Operational Risk: <£50k/year
   - Compliance Risk: 0 violations
   - Fraud Risk: <£100k/year
   - Technology Risk: 99.9% uptime
   
   Compliant? ☐ Yes ☐ No ☐ Needs Monitoring

10. MITIGATION ACTIONS
    If residual risk exceeds appetite, what additional controls are needed?
    
    Action | Owner | Target Date | Expected Impact
    ---|---|---|---
    [e.g., "Implement document verification tool"] | [Risk/Ops] | [Date] | [Reduce likelihood from 4→2]
    
    Residual Risk After Mitigation:
    [Recalculate with new controls implemented]

11. ACCOUNTABILITY
    Who is accountable for managing this risk?
    
    Risk Owner: [Name, Title]
    Escalation Contact: [If risk exceeds tolerance]
    Monitoring Frequency: [Weekly/Monthly/Quarterly]
    Review Date: [When to reassess]
```

---

## PART 2: SAMPLE RISK ASSESSMENT

### Example: Account Opening Process — Data Quality Risk

```
RISK ASSESSMENT FORM
Date Prepared: 15/01/2026
Prepared By: Rachel Green, Risk Manager
Reviewed By: James Wilson, Head of Risk
Assessment Period: Oct 1 – Dec 31, 2025
Risk ID: RISK-2026-001

Process Name: Online Account Opening
Business Unit: Retail Banking
Process Owner: Head of Customer Service
```

---

**RISK #1: Customer Data Entry Error**

```
1. RISK DESCRIPTION
   Customer completes online account opening form with incorrect personal information 
   (wrong name spelling, incorrect DOB, incorrect address). System accepts entry and 
   creates account with bad data. Later regulatory verification finds discrepancies.

2. RISK CATEGORY
   ☐ Credit Risk
   ☑ Operational Risk
   ☐ Compliance Risk
   ☐ Technology Risk
   ☐ Fraud Risk
   ☐ Reputational Risk

3. RISK SOURCE
   ☑ Internal Process
   ☑ People (Customer error, staff oversight)
   ☐ Systems
   ☐ External

4. LIKELIHOOD ASSESSMENT
   Likelihood Score: 3 (Medium)
   
   Supporting Evidence:
   - Historical: 2–3 errors per month (15% of 200 new accounts)
   - Trend: Flat (no improvement since Q3)
   - Cause: Online form lacks real-time validation
   - Industry benchmark: 10–15% error rate typical

5. IMPACT ASSESSMENT
   
   Financial: £20k/year (rework, staff time)
   Regulatory: Medium (data accuracy requirement per GDPR)
   Customer: Low (1–2 customers affected per occurrence)
   Operational: Medium (rework, processing delay)
   Reputational: Low (internal issue, not visible to customer)
   
   Impact Score: 3 (Moderate)

6. RISK SCORING
   Risk Score = 3 × 3 = 9 (Medium Risk)

7. EXISTING CONTROLS
   
   Control | Owner | Effectiveness | Evidence
   ---|---|---|---
   Online form validation (name field required, format check) | Technology | Adequate | Catches empty/invalid formats
   Manual review of application before account creation | CSR | Adequate | CSRs spot ~80% of errors
   Post-opening verification (confirmation call/email) | CSR | Weak | Only some customers respond
   System test of data import | Technology | Strong | System validates account creation
   
   Overall Effectiveness: ~60% (controls catch most errors, but some slip through)

8. RESIDUAL RISK
   Residual Risk = 9 × (1 – 0.60) = 3.6 ≈ 4 (Low-Medium Risk)

9. RISK APPETITE TOLERANCE
   Operational Risk Target: <£50k/year
   Current Impact: ~£20k/year ✅ (Within tolerance)
   Residual Risk: Low-Medium
   
   Compliant? ☑ Yes (but close to tolerance)

10. MITIGATION ACTIONS
    
    Action | Owner | Target Date | Expected Impact
    ---|---|---|---
    Implement client-side form validation (real-time) | Technology | 2026-02-28 | Catch 90% of errors pre-submission
    Require confirmation of key fields (name, DOB) | Technology | 2026-02-28 | Reduce entry errors by 50%
    Automated post-opening verification call (IVR) | Customer Service | 2026-03-31 | Catch 100% of remaining errors
    
    Post-Mitigation Residual Risk:
    Likelihood: 2 (Low) → Risk Score: 2 × 3 = 6
    Residual Risk = 6 × (1 – 0.90) = 0.6 ≈ 1 (Low Risk) ✅

11. ACCOUNTABILITY
    Risk Owner: Head of Customer Service
    Escalation Contact: Head of Risk (if errors exceed £50k/year)
    Monitoring: Monthly (error rate dashboard)
    Review Date: 2026-04-15
```

---

## PART 3: RISK ASSESSMENT GOVERNANCE

### When to Perform Risk Assessment

**Mandatory:**
- New process implementation
- New product launch
- Major system change
- Regulatory requirement change
- Annually for all critical processes

**Recommended:**
- After significant incident
- When process metrics degrade
- Upon management request
- After external audit finding

### Escalation & Approval

| Risk Score | Approval Required | Timeline |
|---|---|---|
| **1–5 (Low)** | Department Head | 10 days |
| **6–12 (Medium)** | Head of Risk | 5 days |
| **13–18 (High)** | Risk Committee + Exec | 2 days |
| **19–25 (Critical)** | Board + CEO | 24 hours |

### Distribution

| Risk Level | Distribution | Frequency |
|---|---|---|
| **Low** | Department Head, Risk Team | Quarterly |
| **Medium** | Exec Committee | Monthly |
| **High** | Risk Committee | Weekly |
| **Critical** | Board | Immediate |

---

## PART 4: RISK SCORING GUIDANCE

### Likelihood Scale (1–5)

**1 = Remote** (Unlikely in foreseeable future)
- Example: Perfect security system never breached
- Frequency: <1 event per 10 years

**2 = Low** (May occur in 3+ years)
- Example: 1 fraud case per year
- Frequency: 1 event per 3–10 years

**3 = Medium** (May occur in 1–3 years)
- Example: 2–5 data entry errors per month
- Frequency: 1 event per 1–3 years

**4 = High** (Likely in <1 year)
- Example: Regular system downtime (quarterly)
- Frequency: 4+ events per year

**5 = Very High** (Could occur multiple times per year)
- Example: Daily transaction processing issues
- Frequency: Weekly or more

### Impact Scale (1–5)

**1 = Negligible**
- Financial: <£10k
- Customers: 1–5
- Regulatory: No impact

**2 = Minor**
- Financial: £10k–£100k
- Customers: 5–25
- Regulatory: No formal action

**3 = Moderate**
- Financial: £100k–£500k
- Customers: 25–100
- Regulatory: Review, minor guidance

**4 = Major**
- Financial: £500k–£1M
- Customers: 100–500
- Regulatory: Fine, restrictions

**5 = Critical**
- Financial: >£1M
- Customers: 500+
- Regulatory: License suspension, criminal action

---

## PART 5: DOCUMENTATION & FILING

### Required Documentation

For each risk assessment, keep on file:
- [ ] Original risk assessment form (signed)
- [ ] Supporting evidence (test results, incident logs)
- [ ] Control evaluation documentation
- [ ] Mitigation action plan
- [ ] Approval signatures
- [ ] Implementation tracking

### Retention

- **Keep:** 7 years (supports audit trail, regulatory inquiries)
- **Location:** Secure risk repository (encrypted)
- **Access:** Risk team + process owner only

### Version Control

Each risk assessment should include:
- Version number
- Date prepared
- Date reviewed
- Changes since last assessment
- Next review date

---

## PART 6: COMMON MISTAKES (Don't Make These!)

❌ **Mistake 1:** Score risk without considering existing controls  
✅ **Correct:** Calculate residual risk (risk after controls)

❌ **Mistake 2:** Assume low probability because it hasn't happened recently  
✅ **Correct:** Use industry benchmarks, historical data, expert judgment

❌ **Mistake 3:** Create mitigation actions without owner and deadline  
✅ **Correct:** Each action must have owner, target date, expected impact

❌ **Mistake 4:** Never revisit risk assessment after mitigation  
✅ **Correct:** Retest controls and recalculate residual risk quarterly

❌ **Mistake 5:** Keep risk assessment only with department owner  
✅ **Correct:** File with Risk team for consolidated view

---

## Related Documents

- [[risk_appetite_statement_2026]] — Risk tolerance limits
- [[policy_operational_risk]] — Risk management framework
- [[escalation_matrix]] — Approval authorities
- [[incident_response_plan]] — How to respond if risk occurs

---

## Document Version History

| Version | Date | Change | Author |
|---------|------|--------|--------|
| 1.0 | 2026-01-15 | Initial template and guidance | Head of Risk |

---
