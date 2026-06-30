# FirstUK Bank — Credit Risk Assessment Guide (Mortgage Underwriting)

## Metadata

| Field | Value |
|-------|-------|
| Document ID | CREDIT-RISK-GUIDE-001 |
| Version | 1.0 |
| Effective Date | 2026-01-15 |
| Review Date | 2027-01-15 |
| Owner | Head of Risk |
| Department | Risk, Lending |
| Status | Active |
| Classification | Internal |
| Audience | Mortgage Underwriters, Risk Analysts, Lending Team |
| Related Documents | [[risk_appetite_statement_2026]], [[policy_operational_risk]], [[risk_assessment_template]], [[escalation_matrix]] |

---

## Purpose

Provide standardized procedures for assessing credit risk on residential mortgage applications, ensuring consistent lending decisions aligned with FirstUK Bank's risk appetite (<0.5% loss ratio).

---

## PART 1: CREDIT RISK FRAMEWORK

### Risk Appetite

**FirstUK Bank Mortgage Credit Risk Target:**
- **Maximum Loss Ratio:** 0.5% annually (max £2.5k loss per £500k portfolio)
- **Default Rate Target:** <2% (max 2% of active mortgages in default at any time)
- **LTV Limit:** Max 95% (no negative equity scenarios)
- **Debt-to-Income Limit:** Max 45% (borrower income constraint)

### Key Metrics

| Metric | Target | Actual (Q4 2025) | Status |
|---|---|---|---|
| **Loss Ratio** | <0.5% | 0.23% | ✅ Within Appetite |
| **Default Rate** | <2% | 1.2% | ✅ Within Appetite |
| **Average LTV** | <80% | 72% | ✅ Conservative |
| **Debt-to-Income** | <45% | 38% | ✅ Conservative |

---

## PART 2: UNDERWRITING PROCESS

### Step 1: Application Assessment

**For Each Mortgage Application:**

```
Borrower Information
- Name, DOB, marital status
- Employment (employer, position, length of service)
- Income (salary, bonus, benefits)
- Credit history (CIBIL score, past defaults)
- Assets (savings, investments)
- Liabilities (other loans, credit cards, obligations)

Property Information
- Property type (house, flat, commercial)
- Location (postcode, area risk assessment)
- Property value (professional valuation)
- Mortgage amount requested
- Loan term (years)
- Fixed vs. variable rate requested
```

---

### Step 2: Borrower Credit Scoring

**Automated Credit Score (0–100):**

| Factor | Weight | Assessment |
|---|---|---|
| **Payment History** | 35% | No missed payments (excellent) → Regular late payments (poor) |
| **Credit Utilization** | 20% | Low balance on cards (good) → High balance (poor) |
| **Credit Age** | 15% | Longer credit history (good) → New credit only (poor) |
| **Credit Inquiries** | 10% | Few recent inquiries (good) → Many recent inquiries (poor) |
| **Default/Delinquency** | 20% | No defaults (excellent) → Recent defaults (poor) |

**Credit Score Ranges:**

| Score | Rating | Risk | Action |
|---|---|---|---|
| **90–100** | Excellent | Very Low | Auto-Approve (if other criteria met) |
| **80–89** | Good | Low | Standard Underwriting |
| **70–79** | Fair | Medium | Enhanced Review |
| **60–69** | Poor | High | Manager Review (may decline) |
| **<60** | Very Poor | Very High | Decline |

---

### Step 3: Income Assessment

**Verify & Qualify Income:**

**Primary Income (Employment):**
- Verify with employer (letter on company letterhead)
- Check minimum 2 years employment history
- Assess income stability (stable job vs. new employment)
- Calculate qualifying income (conservative estimate)

| Employment Type | Qualification Factor |
|---|---|
| **Permanent, 2+ years** | 100% of stated salary |
| **Permanent, <2 years** | 75% of stated salary |
| **Contract, >1 year remaining** | 75% of stated salary |
| **Self-employed, 2+ years** | 75% of average last 2 years |
| **Self-employed, <2 years** | Not acceptable |
| **Bonus/Commission** | 50% of average last 2 years |

**Secondary Income (if applicable):**
- Rental income: 75% (accounts for vacancy)
- Dividend income: 100% (if sustainable, >2 years history)
- Pension/benefits: 100% (if guaranteed, stable)
- Spouse income: Only if married/civil partnership

**Income Calculation Example:**

```
Primary Income: £50,000 (permanent, 3 years) → 100% = £50,000
Bonus: £5,000 (consistent 2 years) → 50% = £2,500
Rental Income: £8,000/year (2+ years) → 75% = £6,000

Total Qualifying Income: £58,500
```

---

### Step 4: Debt Assessment

**Calculate Total Monthly Debt Obligations:**

| Debt Type | Included |
|---|---|
| **Car Loans** | Yes (monthly payment) |
| **Credit Cards** | Yes (5% of balance, or minimum payment, whichever higher) |
| **Student Loans** | Yes (actual payment) |
| **Child Support** | Yes |
| **Alimony** | Yes |
| **Proposed Mortgage** | Yes (calculate below) |
| **Other Mortgages** | Yes (existing property) |

**Calculate Proposed Mortgage Payment:**

```
Mortgage Amount: £300,000
Interest Rate: 4.5% (current offer)
Loan Term: 25 years (300 months)

Monthly Payment = P × [r(1+r)^n] / [(1+r)^n – 1]
Where: P = principal, r = monthly rate, n = months

Monthly Payment = £300,000 × [0.00375(1.00375)^300] / [(1.00375)^300 – 1]
= £1,519

Debt-to-Income Ratio = (Monthly Debt + Mortgage) / Monthly Income
= (£500 other debt + £1,519 mortgage) / £4,875 gross income
= 41.5%
```

---

### Step 5: LTV (Loan-to-Value) Assessment

**Calculate LTV Ratio:**

```
LTV = Mortgage Amount / Property Value × 100%

Example:
Mortgage Amount: £300,000
Property Value: £400,000
LTV = £300,000 / £400,000 = 75%

LTV Risk Assessment:
≤70% = Low Risk (strong equity buffer)
70–80% = Moderate Risk (acceptable)
80–90% = Higher Risk (more careful review)
90–95% = High Risk (requires strong credit/income)
>95% = Very High Risk (likely decline)
```

**FirstUK Bank LTV Limits by Credit Grade:**

| Credit Grade | Max LTV | Pricing Impact |
|---|---|---|
| **Excellent (90+)** | 95% | Standard rate |
| **Good (80–89)** | 90% | +0.25% rate premium |
| **Fair (70–79)** | 85% | +0.50% rate premium |
| **Poor (60–69)** | 80% | May decline |
| **Very Poor (<60)** | N/A | Decline |

---

### Step 6: Property Valuation & Risk

**Property Value Assessment:**

**Risk Factors:**

| Factor | Low Risk | Medium Risk | High Risk |
|---|---|---|---|
| **Location** | Prime area, stable values | Growing suburban | Declining area |
| **Property Type** | House, flat | Newer construction | Listed building, unusual |
| **Condition** | Modern, good repair | Average condition | Poor, repairs needed |
| **Market Liquidity** | Easy to sell, buyers | Moderate sales | Hard to sell, few buyers |

**Valuation Approach:**
1. Order independent professional valuation (surveyor)
2. Compare to comparable properties (market data)
3. Apply location/condition adjustments
4. Confirm valuation supports LTV ratio

**If Valuation < Purchase Price:**
- Reduction triggers higher LTV
- May require renegotiation or additional deposit
- May require borrower to make up shortfall

---

### Step 7: Comprehensive Risk Score

**Calculate Composite Risk Score (0–100):**

| Component | Weight | Score | Weighted |
|---|---|---|---|
| **Credit Score** | 40% | 82 | 32.8 |
| **Income Stability** | 25% | 85 | 21.3 |
| **Debt-to-Income** | 20% | 75 | 15.0 |
| **LTV Ratio** | 15% | 80 | 12.0 |
| **TOTAL SCORE** | **100%** | — | **81.1** |

**Risk Score Interpretation:**

| Score | Rating | Action |
|---|---|---|
| **85–100** | Excellent | Auto-Approve (delegate authority) |
| **75–84** | Good | Standard Approval (manager sign-off) |
| **65–74** | Fair | Enhanced Review (risk manager review) |
| **55–64** | Poor | Senior Review (head of lending) |
| **<55** | Very Poor | Decline |

---

## PART 3: UNDERWRITING DECISION MATRIX

**Use This Matrix to Make Lending Decisions:**

| Credit Score | Income DTI | LTV | Decision | Notes |
|---|---|---|---|---|
| 90+ | <35% | <80% | **APPROVE** | Strong candidate |
| 90+ | 35–45% | <85% | **APPROVE** | Good candidate |
| 80–89 | <40% | <90% | **APPROVE** | Standard |
| 80–89 | 40–45% | <80% | **APPROVE** | Monitor |
| 70–79 | <35% | <75% | **CONDITIONAL** | Requires depth review |
| 70–79 | 35–45% | >75% | **CONDITIONAL** | Risk review needed |
| 60–69 | Any | Any | **DECLINE** | High default risk |
| <60 | Any | Any | **DECLINE** | Very high risk |

---

## PART 4: CONDITIONAL APPROVALS

**"Conditional Approval" means:** Approved IF conditions are met

**Common Conditions:**

1. **Financial Documentation**
   - "Approved upon receipt of recent bank statements (last 3 months)"
   - "Approved upon employer letter confirming income and employment"

2. **Property-Related**
   - "Approved upon satisfactory building/structural report"
   - "Approved upon removal of subsidence caveat"

3. **Additional Funds**
   - "Approved upon borrower increasing down payment to £[amount]"
   - "Approved upon co-signer with strong credit joining application"

4. **Debt Reduction**
   - "Approved upon payoff of auto loan (outstanding £[amount])"
   - "Approved upon credit card balance reduction to <£[amount]"

5. **Insurance**
   - "Approved upon evidence of buildings insurance obtained"

**Condition Management:**
- [ ] Communicate conditions to borrower
- [ ] Set deadline for condition satisfaction (typically 10 days)
- [ ] Track conditions in system
- [ ] Release funds only after all conditions met
- [ ] Document condition satisfaction

---

## PART 5: DECLINE SCENARIOS

**When to Decline Application:**

```
MANDATORY DECLINE (No Review):
- Credit score <60 (very high default risk)
- DTI >45% (unaffordable)
- LTV >95% (no equity cushion)
- Fraud/misrepresentation detected
- Adverse criminal history
- Recent bankruptcy (<2 years)

DISCRETIONARY DECLINE (May Waive with Senior Approval):
- Credit score 60–69 (requires exceptional other credentials)
- Employment <1 year in unstable field
- Property in high-risk declining market
- Self-employed <2 years (marginal data)
```

**Decline Letter to Borrower:**

```
Dear [Borrower],

We have reviewed your mortgage application dated [date] for 
the property at [address].

Decision: APPLICATION DECLINED

Reason: [e.g., "Insufficient qualifying income based on your 
current employment"]

Key Factors Considered:
- Income: Requiring [amount], you show [amount]
- Credit History: Recent late payment on [account]
- Debt Obligations: [Total monthly debt] exceeds [acceptable level]

Next Steps:
We recommend:
1. Increase down payment to reduce loan amount
2. Resolve outstanding credit issues
3. Reapply in [months/years] when [condition] is satisfied

You have the right to receive a copy of your credit report 
and dispute any errors. Contact [FCA complain address].

We appreciate your interest in FirstUK Bank.

Best regards,
Lending Team
```

---

## PART 6: DISBURSEMENT & POST-APPROVAL

### Pre-Disbursement Checks

**Before Releasing Funds:**

- [ ] Final walkthrough by borrower and appraiser (no major changes)
- [ ] Final verification of employment (phone call 1–2 days before closing)
- [ ] Final credit check (no new derogatory marks, new debt)
- [ ] Title search confirms clear title (no liens, claims)
- [ ] Insurance policy obtained and paid
- [ ] All conditions satisfied
- [ ] Closing disclosure reviewed and signed

### Mortgage Servicing

**Post-Approval Monitoring:**

- Monthly payment receipt and processing
- Annual statement verification
- Ongoing credit monitoring (re-score annually)
- Property value tracking (valuation every 3 years)
- Default early warning (missed payment triggers action)

---

## PART 7: LOSS MITIGATION

**If Borrower Enters Default:**

**Timeline:**

```
Day 0: Payment missed
Day 15: Late notice sent (15-day courtesy letter)
Day 30: First formal notice (demand for payment)
Day 60: Second formal notice (right to cure period)
Day 90: Default declared (cascade proceedings initiated)
Day 120+: Foreclosure/sale begins
```

**Mitigation Options (Offered to Borrower):**

1. **Payment Plan** — Extend missed payments over time
2. **Loan Modification** — Change terms (extend, reduce rate)
3. **Forbearance** — Temporarily reduce/skip payments
4. **Short Sale** — Sell property below owed amount
5. **Deed in Lieu** — Transfer property back to bank instead of foreclosure

**Loss Assessment:**

If foreclosure proceeds:
```
Sale Price: £350,000 (property declined from £400,000)
Less: Realtor fees (6%): £21,000
Less: Legal/court costs: £5,000
Less: Property taxes/HOA: £2,000
Net Proceeds: £322,000

Loan Balance: £290,000 (remaining principal)
Borrower Equity: £32,000 (proceeds cover loan)

If Sale Price < Loan Balance: FirstUK Bank absorbs loss
Loss = Loan Balance – Sale Proceeds
```

---

## PART 8: REGULATORY ALIGNMENT

### Fair Lending Compliance

**FirstUK Bank Does NOT Consider:**

❌ Race, color, national origin  
❌ Religion  
❌ Sex, gender identity  
❌ Age (except as required by law)  
❌ Marital status  
❌ Disability (must accommodate)  

**FirstUK Bank DOES Consider:**

✅ Credit score, payment history  
✅ Income and employment stability  
✅ Debt obligations  
✅ Property value and location  
✅ Loan-to-value ratio  
✅ Down payment amount  

**Documentation:** Keep records demonstrating decisions based on credit criteria, not protected characteristics.

---

## PART 9: COMMON APPROVAL MISTAKES (Don't Make These!)

❌ **Mistake 1:** Overlook recent late payments (weight of recent payment history)  
✅ **Correct:** Weight recent activity (last 24 months) more heavily

❌ **Mistake 2:** Accept stated income without verification  
✅ **Correct:** Verify income with tax returns, W2s, employer letters

❌ **Mistake 3:** Ignore DTI when credit score is excellent  
✅ **Correct:** High DTI increases default risk regardless of credit score

❌ **Mistake 4:** Approve at max LTV without strong credit and income  
✅ **Correct:** Higher LTV requires stronger credit and income profile

❌ **Mistake 5:** Waive conditions without clear authorization  
✅ **Correct:** Conditions exist to reduce risk; document any waivers

---

## PART 10: ESCALATION & APPROVAL AUTHORITY

| Scenario | Approval Level | Timeline |
|---|---|---|
| **Standard Approval (Score 75+, DTI <40%, LTV <85%)** | CSR/Underwriter | 2–3 days |
| **Conditional Approval (Minor conditions, good credit)** | Manager | 3–5 days |
| **Enhanced Review (Fair credit, edge cases)** | Risk Manager | 5–7 days |
| **Decline (Weak profile)** | Risk Manager | 2–3 days |
| **Appeal (Borrower disputes decline)** | Head of Lending | 10 days |

---

## Related Documents

- [[risk_appetite_statement_2026]] — FirstUK's risk tolerance
- [[policy_operational_risk]] — Risk management framework
- [[risk_assessment_template]] — Comprehensive risk assessment procedures
- [[escalation_matrix]] — Approval authority levels
- [[incident_response_plan]] — If fraud or errors detected

---

## Document Version History

| Version | Date | Change | Author |
|---------|------|--------|--------|
| 1.0 | 2026-01-15 | Initial credit risk guide | Head of Risk |

---

## Sign-Off

**Approved by:**  
Head of Risk — **Date: 2026-01-15**  
Chief Lending Officer — **Date: 2026-01-15**  
Compliance Officer — **Date: 2026-01-15**

---
