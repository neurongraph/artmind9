# Benchmark run: banking_corpus_after_phase8_cutover

- Source: `/Users/surjitdas/Projects/artmind9/benchmarking/questions.md`
- Domain hint: (none — agent routed itself)
- Backend: claude-sdk
- Status: completed (36/36)

## Q01 — Supporting a vulnerable customer changing address

**Question:** A vulnerable customer is moving home and finds forms difficult. How should I help them change their address safely, including identity checks, affected accounts, and any escalation?

**Answer** (completed, 11 turns, 59.5s, $0.1645):

I'll search the artmind system for guidance on helping vulnerable customers change their address safely. Let me start by discovering which domains contain this information.Perfect. The most directly relevant domains are:
- **banking.sop_guides** — has a "sop_change_of_address.md" document 
- **banking.communications** — likely has guidance on vulnerable customer interactions
- **banking.policy** — may contain identity verification and safeguarding policies
- **banking** — has a structured table on "vulnerable_customers"

Let me retrieve the specific SOP for address changes and relevant policies:The output is large. Let me read the full document:Now let me check for guidance on helping vulnerable customers specifically:Let me check the escalation matrix and vulnerable customers table:Perfect, there's a vulnerable_customers table. Let me retrieve its schema:Now let me look at the escalation matrix:Now let me look at the branch operations training to understand vulnerable customer support better:Excellent. Now I have comprehensive information. Let me compile the answer with proper grounding.

---

## How to Help a Vulnerable Customer Change Their Address Safely

Based on FirstUK Bank's procedures in **sop_change_of_address.md** (banking.sop_guides) and **branch_operations_training.md** (banking.communications), here's your step-by-step approach:

### **1. Identify the Customer's Vulnerability & Support Needs**

First, check if the customer is flagged in the **vulnerable_customers** table (banking domain). They may need:
- **Extra Time:** Allow slower pace, break explanation into steps
- **Interpreter:** Arrange language support if needed
- **Safe Space:** Offer private/quiet area for sensitive discussions
- **Priority Routing:** Escalate to specialized team if complex

**Communication approach** (from branch_operations_training.md):
- Speak clearly and face the customer
- Use plain language (no jargon like "KYC" or "AML screening")
- Offer written information in addition to verbal
- Allow extra time—don't rush

---

### **2. Verify Customer Identity (Simplified for Vulnerability)**

For a vulnerable customer finding forms difficult:

**Preferred:** In-person branch visit (lowest risk, most accessible)
- Ask for government-issued ID (Passport/Driving License)
- Compare photo to customer face
- Document in system

**Alternative:** Phone verification if branch visit is difficult
- Confirm security questions
- Verify date of birth
- Last 4 digits of account number
- Mother's maiden name

Don't demand complex documentation if the customer is struggling—escalate to manager if identity cannot be easily verified.

---

### **3. Identify All Affected Accounts**

Ask: "Which accounts do you want the address changed for?"

Common scenarios:
- **Single savings account** → Straightforward
- **Multiple accounts** (savings + current) → Clarify if same address for all
- **Joint account** → Both holders must consent (write down both names)
- **Mortgage** → Keep property address unchanged; update personal address only

---

### **4. Collect & Validate New Address**

**For UK moves (easiest):**
- Full postal address
- Postcode
- Use Royal Mail lookup tool to verify postcode matches town

**For international moves (requires more steps):**
- Full address in destination country
- Country name
- Will require proof of address

**Form assistance:** If the customer finds forms difficult, you can:
- Fill out form together verbally
- Have them dictate address to you
- Provide written copy to verify before submission

---

### **5. Assess AML Risk & Identity Verification Requirement**

**Low-risk (no extra documents needed):**
- Moving within UK
- Established customer (>5 years)
- No sanctions concerns

**Medium/High-risk (proof of address required):**
- Moving to international address
- New customer (<5 years)
- Moving to high-risk country

**Acceptable proof of address:**
- Utility bill (within 3 months)
- Council tax letter (current year)
- Bank statement (within 3 months)
- Mortgage statement (current year)

**Help them get it:** Offer alternatives:
- "Can you ask your utility company for a letter?"
- "Does your employer letter show your new address?"
- If they can't obtain proof → Don't block the change. Process with flag "Proof pending" (30-day deadline).

---

### **6. Update All Systems & Accounts**

Once verified, update:
- Core account address
- Statement delivery address
- Standing orders & Direct Debits (notify payers)
- Communications (email, phone, post)

---

### **7. Escalate If:**

- Customer disputes the change
- Address in high-risk country (escalate to Compliance Head)
- Proof of address cannot be obtained after 30 days
- Suspected fraud
- Customer is flagged for financial abuse (escalate to Compliance)

**Who to escalate to:**
- **Branch Manager:** Identity verification issues, customer disputes
- **Compliance Head:** AML concerns, high-risk countries
- **Head of Operations:** Process blocked >5 days

---

### **8. Communicate Change Clearly**

Send **confirmation by email & post**:
- Email: Summarize change, confirm new address, explain next steps
- Post (to old address): Security check to detect account takeover fraud
- **Include:** Contact details for fraud reporting (0800-555-2265)

---

### **Key Documents Grounded In**

- **sop_change_of_address.md** (banking.sop_guides) — Full 12-step process, identity verification, AML screening, affected accounts
- **branch_operations_training.md** (banking.communications) — Vulnerable customer communication: "Allow extra time for older/vulnerable customers," "Arrange interpreter," "Use plain language"
- **escalation_matrix.md** (banking.sop_guides) — Who handles what level of complexity
- **vulnerable_customers** table (banking domain) — Support needs tracked: Extra Time, Interpreter, Safe Space, Priority Routing

---

**Bottom line:** Don't make a vulnerable customer struggle with forms. Use plain language, allow extra time, offer alternatives (branch visit, phone), and escalate if identity verification or proof of address is difficult to obtain—don't block the change indefinitely.

**Evaluation comment (grading notes, not shown to artmind):** Tests a realistic multi-document operational response. It should connect the customer-treatment guidance in `guides/complaint_resolution_guide.md` and/or `training/branch_operations_training.md` with the verification, multi-account, joint-account, mortgage, and escalation steps in `sop_procedures/sop_change_of_address.md`.

## Q02 — Repeated standing-order failures

**Question:** A customer says their monthly standing order keeps failing. What should I check, what happens after a failed attempt, and what are the customer’s options?

**Answer** (completed, 5 turns, 22.0s, $0.0559):

I'll search the artmind system for information about standing order failures, what happens after a failed attempt, and customer options.I can see there's a domain `banking.sop_guides` with a document `sop_standing_orders.md` which is exactly what I need. Let me retrieve information about standing order failures from that domain.Perfect! I have found comprehensive information from the Standing Order SOP document. Here's what you should check and know about failed standing orders:

## What to Check When a Standing Order Fails

According to the SOP (sop_standing_orders.md from banking.sop_guides), the common failure reasons are:
- **Insufficient funds** in the source account
- **Invalid recipient account number**
- **Recipient account closed**
- **Payee bank system failure**
- **Duplicate prevention** (if the payment failed before)

## What Happens After a Failed Attempt

The SOP outlines a structured 5-step failure handling process:

1. **Detection** — System logs the failed attempt and marks the standing order status as "Failed"

2. **Customer Alert** — Email or SMS is sent to the customer including:
   - The amount
   - The recipient name
   - The failure reason
   - A request for action (provide funds or update payee information)

3. **Automatic Retry** — The system retries the payment once (usually within 24 hours). If successful, the standing order resumes normally.

4. **Escalation** — If the retry fails, the standing order is suspended and requires staff review. The bank contacts the customer to resolve the issue.

5. **Resolution** — The customer either provides corrected information (updated account details, funds, etc.) and the standing order resumes, or they opt to cancel it.

## Customer's Options

After a failed standing order, the customer can:
- **Provide corrected information** — Update the payee account number or sort code if those were incorrect
- **Add funds** — Ensure sufficient balance in their source account for the next retry attempt
- **Modify the standing order** — Change the amount, frequency, or payee
- **Cancel the standing order** — Stop the standing order entirely with no notice period required (cancellation is free and effective immediately)

The bank will contact the customer to discuss these options after the initial failure and retry attempt.

**Source:** sop_standing_orders.md (banking.sop_guides)

**Evaluation comment (grading notes, not shown to artmind):** Tests process retrieval with a useful causal chain. The answer should use `sop_procedures/sop_standing_orders.md` to cover common failure causes, notification, one retry, staff review/suspension, correction or cancellation, rather than confusing a standing order with a direct debit.

## Q03 — Stop a direct debit versus a standing order

**Question:** What is the difference between cancelling a direct debit and cancelling a standing order, and when does each cancellation take effect?

**Answer** (completed, 6 turns, 24.8s, $0.0718):

The question is about direct debits and standing orders. I can see the domain `banking.sop_guides` contains documents specifically about these: `sop_direct_debits.md` and `sop_standing_orders.md`. Let me retrieve and compare them.Now let me retrieve the standing orders SOP to compare:Based on the SOPs from FirstUK Bank's banking.sop_guides domain, here are the key differences and cancellation timelines:

## Key Differences

| Aspect | Direct Debit | Standing Order |
|--------|---|---|
| **Initiator** | Biller/Company pulls money | You (customer) push money |
| **Amount** | Variable (can change, e.g., utility bills) | Fixed amount each time |
| **Control** | Biller sends payment instructions | You tell bank what to pay |
| **Protection** | Full Direct Debit Guarantee (£15k refund) | Limited protection |

## Cancellation Differences

**Direct Debit Cancellation:**
- **How:** Contact FirstUK (phone: 0800-555-2265, email, branch, or online form) OR contact the biller
- **When it takes effect:** **Immediate** — no notice period required
- **Pending payments:** Blocked if cancellation occurs before the settlement date
- **Process:** FirstUK sets mandate status to "Canceled," notifies the biller automatically via ARUDD cancellation message, and sends customer confirmation

**Standing Order Cancellation:**
- **How:** Online self-service, phone (0800-555-2265), branch, or mail
- **When it takes effect:** **Immediately** — no notice period required
- **Pending payments:** Next scheduled payment is blocked
- **Process:** Customer requests cancellation, staff verifies identity, status set to "Cancelled," and customer receives email confirmation

## Takeaway

Both can be cancelled immediately with no notice period. The main operational difference is that **direct debits** require notification to the biller (handled by the bank), while **standing orders** are entirely customer-controlled. Direct debits also provide stronger consumer protections under the Direct Debit Guarantee scheme.

**Sources:**
- sop_direct_debits.md (banking.sop_guides domain) — Sections on mandate cancellation and effective dates
- sop_standing_orders.md (banking.sop_guides domain) — Sections on standing order cancellation process

**Evaluation comment (grading notes, not shown to artmind):** Tests entity distinction across closely related payment processes. Reconcile `sop_procedures/sop_direct_debits.md` and `sop_procedures/sop_standing_orders.md`: both describe immediate cancellation, but the mandate/biller relationship and the customer-controlled payment instruction are different.

## Q04 — SmartSaver features and rate structure

**Question:** What are the key features of the SmartSaver account, and how does its interest rate vary by balance?

**Answer** (completed, 13 turns, 58.1s, $0.1032):

I'll search the artmind system for information about the SmartSaver account.Now I'll check the banking.products domain, which has a SmartSaver-specific document:Let me resolve the SmartSaver account first:Now let me get detailed context on the SmartSaver product:Good! Now let me get more details on the SmartSaver interest rate structure. Let me search for the interest rate tiers:Now let me search for more comprehensive information about SmartSaver features and complete rate structure. Let me check the terms and conditions document:Let me fetch the specific chunks that detail the features and rates:Let me correct the syntax:Let me use vector-text to search for comprehensive SmartSaver features and rates:Perfect! Now let me search for additional SmartSaver features beyond interest rates:Excellent! I now have comprehensive information to answer the question. Let me provide the answer with proper grounding.

## SmartSaver Account: Key Features and Interest Rate Structure

Based on documents from **banking.products** and **banking.reference** domains:

### Key Features

The SmartSaver account offers:

- **No Monthly Fees** — £0 maintenance fee (source: smartsaver_terms_conditions.md)
- **Easy Access** — Withdraw anytime with no penalties or waiting periods via ATM, bank transfer, or branch teller (source: smartsaver_terms_conditions.md)
- **Monthly Interest** — Interest calculated daily and paid on the last business day of each month (source: product_pricing_guide_2026.md)
- **Variable Rate** — Tracks Bank of England Base Rate + 2.5% fixed margin, reviewed monthly (source: product_pricing_guide_2026.md)
- **Low Opening Requirements** — Minimum £1 to open; no minimum balance required (source: website_faq.md)
- **Joint Accounts** — Option to open with another customer with equal withdrawal rights (source: website_faq.md)
- **Optional Fees Only** — International ATM withdrawal (£2.50), international transfer (£5.00), card replacement (£15.00 standard/£25 expedited) (source: product_faq.md)

### Interest Rate Structure by Balance

SmartSaver has a **3-tier interest rate structure** based on account balance:

| Tier | Balance Range | Annual Interest Rate (AER) |
|------|---------------|---------------------------|
| **Tier 1** | £0–£10,000 | **4.50%** |
| **Tier 2** | £10,001–£50,000 | **4.70%** |
| **Tier 3** | £50,001+ | **4.80%** |

*(Source: interest_rate_schedule_2026.md, product_pricing_guide_2026.md)*

### Upgrade Path: SmartSaver Plus

When a customer's balance exceeds £50,001, they automatically upgrade to **SmartSaver Plus** (no application needed), which offers:
- **Higher rate: 5.25% AER** (vs. 4.80% standard Tier 3)
- Dedicated account manager
- Priority customer service
- No international transfer fees

If the balance subsequently drops below £50,001, the customer reverts to standard SmartSaver Tier 3 rates (4.80%).

**Evaluation comment (grading notes, not shown to artmind):** Baseline product question. Combine the product terms and features in `products/smartsaver_terms_conditions.md` with the tiered AER, daily accrual, monthly payment, and variable-rate basis in `reference/interest_rate_schedule_2026.md`.

## Q05 — Incorrect-interest complaint

**Question:** A customer says their savings interest was calculated incorrectly. How should we investigate and resolve the complaint?

**Answer** (completed, 7 turns, 34.9s, $0.0805):

I'll search the knowledge base for information about investigating and resolving savings interest calculation complaints.Multiple domains are relevant: `banking.policy` (complaints policy), `banking.sop_guides` (complaint resolution procedures), `banking.products` (product terms and interest rates), and `banking.reference` (interest rate schedules). Let me retrieve the investigation and resolution procedures.Now let me get details on the specific investigation process for interest calculation errors and product information.Let me get more specific guidance on the investigation steps for interest calculation disputes.Based on the FirstUK Bank documentation, here's a comprehensive framework for investigating and resolving a customer complaint about incorrect savings interest calculation:

## **Investigation & Resolution Process**

**Grounded in:** `complaint_resolution_guide.md` (banking.sop_guides), `escalation_matrix.md` (banking.sop_guides), and interest rate documents (banking.products, banking.reference)

### **Step 1: Acknowledge & Document (Day 1)**
- Issue a **Complaint Reference Number** (e.g., EXC-00012345)
- Assign an investigator
- Send acknowledgment within 1 business day

### **Step 2: Investigation (Days 1–7)**
For interest calculation disputes, verify:

1. **Determine the expected rate:**
   - Check which product the account holds (e.g., SmartSaver, SmartSaver Plus)
   - Confirm the Bank of England base rate on the complaint date
   - Apply the correct product margin (e.g., SmartSaver Plus: BoE Rate + 2.75%)
   - Account for any rate changes (rates reviewed monthly mid-month)

2. **Verify what was actually charged:**
   - Pull account statement for the disputed period
   - Calculate daily interest: balance × daily rate ÷ 365
   - Compare charged interest against expected calculation

3. **Identify the error source:**
   - System calculation error (interest engine applied wrong rate)
   - Our error (applied old rate after update; missed margin adjustment)
   - Policy misunderstanding (customer misunderstood tiered rates or minimums)
   - No error (rate/calculation correct per terms)

4. **Gather evidence:**
   - Account history and rate change log
   - Product terms and current rate schedules
   - System audit trail or calculation records

### **Step 3: Decision (By Day 8)**
Respond with findings and resolution:

- **If our error:** Recalculate correct interest; refund the difference + **additional interest for inconvenience** (goodwill payment £20–£150 depending on loss duration)
- **If policy confusion:** Provide clear explanation of rate structure, tiered benefits, or minimum balance requirements
- **If no error:** Explain why the rate charged is correct per product terms

### **Step 4: Compensation Framework**
According to `escalation_matrix.md`:
- **Minor issue** (small calculation error, quick resolution): £20–£50
- **Moderate issue** (£100–£500 loss, service failure): £50–£150
- **Significant issue** (£500+ loss, multiple failures): £150–£300+

### **Escalation Path**
- **Level 1 (Frontline):** Initial simple fee reversals <£500
- **Level 2 (Supervisor/Manager):** Complaint investigation & resolution £500–£5,000 — 1–3 business days
- **Level 3 (Department Head):** Significant refunds £5,000–£50,000 — 3–5 business days

### **Customer Rights**
- **8-day response guarantee** (your deadline)
- **No retaliation** for complaining
- **6-month window** to escalate to Financial Ombudsman if dissatisfied

**If unresolved after 8 days or customer remains unhappy, customer can escalate to the Financial Ombudsman Service (free, independent, binding on you).**

**Domains:** banking.sop_guides (procedures), banking.products (rate terms), banking.reference (rate schedules)

**Evaluation comment (grading notes, not shown to artmind):** Tests product-to-complaints traversal. It should use the active `policies/policy_complaints_v3.md` for investigation and remedy, plus `reference/interest_rate_schedule_2026.md` and `products/smartsaver_terms_conditions.md` for the rate-calculation evidence. The answer should not assume the customer is correct without reconciliation.

## Q06 — Direct-debit claim and complaint escalation

**Question:** A customer was charged by direct debit after they cancelled it and does not accept the initial decision. What should happen next?

**Answer** (completed, 9 turns, 41.4s, $0.0960):

I'll search the knowledge base for information about handling direct debit charges after cancellation and the dispute resolution process.I'll search the artmind knowledge base for guidance on handling disputed direct debit charges after cancellation. Let me start by identifying the relevant domains.The question involves a disputed direct debit charge after cancellation and what happens when the customer rejects the initial decision. This relates to complaint handling, direct debit procedures, and escalation. Let me query the relevant domains:Let me search for information about escalation and complaint resolution when customers dispute initial decisions:Excellent! I found a specific decision point about customer disagreement. Let me get more details on the direct debit procedures and complaint resolution guide:Perfect! Now let me get the complaint resolution guide for complete information:Let me search for complaint resolution guidance more directly:Perfect! I now have comprehensive information. Let me provide the answer.

## Answer

Based on FirstUK's policy documents, when a customer is charged by direct debit after cancellation and does not accept the initial decision, the following should happen next:

### Escalation Path

**Step 1: Dispute Resolution Process**
The customer can escalate their dispute through the formal complaint resolution process:
- **Manager Escalation** — the initial escalation step where the dispute is raised to a manager for review and decision
- **Formal Complaint Process** — the customer can lodge a formal complaint via:
  - Phone: 0800-555-2265
  - Email: support@firstuk.bank
  - Branch visit
  - Online complaint form at www.firstuk.bank/complaints

**Step 2: Investigation & Decision (within 8 days)**
- FirstUK will conduct a thorough investigation
- Verify the Direct Debit mandate status (whether it was properly cancelled)
- Assess the claim validity
- Provide a formal response explaining their findings and decision

**Step 3: Financial Ombudsman Service Escalation**
If the customer remains dissatisfied, they have two options:
1. **Escalate to Financial Ombudsman Service** — free and independent escalation available if:
   - The customer disagrees with FirstUK's decision
   - FirstUK doesn't respond within 8 days
   - FirstUK doesn't acknowledge receipt
2. **Timeline**: The customer has **6 months** to escalate to the Ombudsman after receiving FirstUK's decision

### Direct Debit Guarantee Protection
Per the Direct Debit Guarantee, if the charge was genuinely unauthorized or occurred after proper cancellation, FirstUK should refund the full amount within 10 business days with no liability to the customer.

### Key Documents Grounding This Answer
- **sop_direct_debits.md** (banking.sop_guides): Direct Debit dispute and claim process, including the scenario "Charged after cancellation"
- **complaint_resolution_guide.md** (banking.sop_guides): Formal complaint escalation procedures, including Financial Ombudsman escalation rights and the 6-month window for escalation

**Evaluation comment (grading notes, not shown to artmind):** Tests hand-off between processes. The answer should follow the Direct Debit Guarantee claim and cancellation evidence in `sop_procedures/sop_direct_debits.md`, then the formal complaint route in `policies/policy_complaints_v3.md` and `guides/complaint_resolution_guide.md`.

## Q07 — Complaint authority before the revision

**Question:** On 15 May 2026, who could approve £300 compensation for a complaint, and why?

**Answer** (completed, 7 turns, 32.2s, $0.0729):

I'll help you find who could approve £300 compensation for a complaint on 15 May 2026. Let me start by exploring the available domains and then retrieve the relevant approval authority information.The question concerns compensation approval, which is most likely covered in banking.policy or banking.sop_guides. Let me search for information about compensation approval authorities and thresholds.Now let me search for information about compensation approval authorities, specifically looking for who can approve £300 compensation.Good! I found relevant compensation frameworks. I can see there are two versions of the policy with different thresholds. Let me check the valid dates of these documents to determine which one was in effect on 15 May 2026.Good! I can see that `policy_complaints.md` was effective from 2026-01-15 and was superseded by `policy_complaints_v3` from 2026-06-01. Since the question asks about 15 May 2026, which is before 1 June 2026, the original policy was in effect. Let me also check the v3 to understand what changed, and confirm the approval structure.

Based on the vector search results and the document I just retrieved, I now have the answer. On 15 May 2026, the **policy_complaints.md** (version 2.0, effective 2026-01-15) was in force, and according to the Compensation Framework in that document:

**Answer:**

On 15 May 2026, a **Director** (specifically the Head of Customer Service) could approve £300 compensation for a complaint.

**Why:**

According to the Compensation Framework in policy_complaints.md (effective 2026-01-15), the approval authorities were structured as follows:

- **<£100**: Customer Service Manager approval
- **£100–£500**: Director approval ← £300 falls in this range
- **>£500**: CEO approval
- **>£1,000**: Board notification + CEO approval

Since £300 falls within the £100–£500 range, the Director (Head of Customer Service) had the authority to approve this compensation amount. The policy also notes that compensation of £100–£300 is typically applied for "service failure (>5 days)" scenarios with "Significant impact" as the basis.

**Grounded in:** policy_complaints.md v2.0, Compensation Framework section and Escalation Matrix section (banking.policy domain)

**Evaluation comment (grading notes, not shown to artmind):** Tests historical authority. The answer must use `policies/policy_complaints.md` (v2.0, effective 2026-01-15) because the question predates v3.0. It should identify the relevant escalation and compensation tables and note their inconsistency instead of presenting an unqualified single approver.

## Q08 — Complaint authority after the revision

**Question:** On 15 June 2026, who can approve £300 compensation for a complaint? Is any earlier guidance still applicable?

**Answer** (completed, 10 turns, 52.9s, $0.0953):

I'll search the artmind system for information about compensation approval authorities. Let me start by discovering what domains contain guidance on this topic.The question about compensation approval is likely covered in `banking.policy` (complaints policy) and `banking.sop_guides` (escalation/complaint handling). Let me check these domains for information about £300 compensation approval.Good. I can see there are complaint-related documents including `policy_complaints.md`, `policy_complaints_v3.md`, and `escalation_matrix.md` which are likely to contain information about compensation approval. Let me search for information about compensation limits and approval authorities.The pattern didn't return results. Let me try a vector search to find relevant text about compensation approval.Excellent. I found guidance, but I can see there are different versions of the compensation policy with different approval limits. Let me check the document dates to understand which is current on 15 June 2026.Now let me check the v3 policy which superseded this one on 2026-06-01.Good! Let me read the key compensation section from v3:Perfect. Now let me verify this is the current policy as of 15 June 2026:

**Answer:**

On **15 June 2026**, a **Complaints Manager** can approve £300 compensation for a complaint.

This is based on **policy_complaints_v3.md** (effective 2026-06-01, still active on 15 June 2026), which states in its Compensation Framework:

> **Limits (aligned with Escalation Matrix above):**
> - <£500: Complaints Manager approval
> - £500–£2,000: Director approval
> - >£2,000: CEO approval
> - >£5,000: Board notification + CEO approval

Since £300 falls within the "<£500" threshold, a **Complaints Manager** holds the approval authority.

**Is earlier guidance still applicable?**

No. The earlier guidance (from **policy_complaints.md**, effective 2026-01-15) is **no longer applicable** as of 15 June 2026. It was explicitly superseded by Version 3.0 on 2026-06-01, which states: *"This policy (Version 3.0, effective 2026-06-01) supersedes and replaces Version 2.0 (effective 2026-01-15) in full."*

The prior version had different thresholds:
- <£100: Customer Service Manager approval
- £100–£500: Director approval

So the authority changed from Director approval to Complaints Manager approval for £300 compensation.

---

**Domains & documents grounded:** banking.policy domain — policy_complaints_v3.md (Compensation Framework, Chunk 22/38), with supersession verified through policy_complaints.md metadata.

**Evaluation comment (grading notes, not shown to artmind):** Tests temporal filtering and formal supersession. `policies/policy_complaints_v3.md` is effective from 2026-06-01 and explicitly supersedes v2.0 in full to resolve the conflicting approval thresholds. A correct answer should apply v3.0 and explicitly exclude the old thresholds.

## Q09 — Explain the policy history, not just the current rule

**Question:** How did complaint-compensation approval rules change in 2026, and what issue prompted the change?

**Answer** (completed, 10 turns, 43.7s, $0.0839):

The question is about complaint-compensation approval rules, which maps to the complaints/policy domains. Let me search the relevant policy and SOP domains:Excellent — I found a key entity: "Escalation and Compensation Policy" (Version 3.0, effective 2026-06-01) that supersedes Version 2.0 and specifically resolves an inconsistency. Let me get full context on this policy:Perfect. Now let me get the specific approval thresholds from the Compensation Framework and Escalation Matrix to show what changed:Let me fetch the full document to see the compensation thresholds:Let me fix the chunks command syntax:Let me try a vector search to find the specific compensation framework details in both versions:Perfect! Now I have all the information needed to answer the question. Let me compile the answer:

## Answer

**Complaint-compensation approval rules changed on 2026-06-01** when **Version 3.0 of the Escalation and Compensation Policy** (effective 2026-06-01) superseded Version 2.0 (effective 2026-01-15).

### The Change

Version 3.0 aligned two previously inconsistent approval thresholds that had contradicted each other in Version 2.0:

**Previous approval thresholds (Version 2.0 — contained internal inconsistency):**
- Escalation Matrix: £500–£2,000 loss = Director approval; >£500 = escalation
- Compensation Framework: £100–£500 = Director approval; >£500 = CEO approval

**New aligned thresholds (Version 3.0):**
- <£500: Complaints Manager approval
- £500–£2,000: Director approval  
- >£2,000: CEO approval
- >£5,000: Board notification + CEO approval

### The Issue That Prompted the Change

The **internal inconsistency between the Escalation Matrix and Compensation Framework sections in Version 2.0** — they specified **different Manager/Director approval boundaries for the same decisions**. This created ambiguity about who had authority to approve compensation awards in the £500–£2,000 range.

**Domain & Sources:**
- **Domain:** banking.policy
- **Documents:** policy_complaints_v3.md (Version 3.0, effective 2026-06-01), superseding policy_complaints.md (Version 2.0)

**Evaluation comment (grading notes, not shown to artmind):** Tests document lineage. The answer should compare the same Document ID, COM-POL-006, across `policies/policy_complaints.md` and `policies/policy_complaints_v3.md`, explain the v2 internal inconsistency, and describe v3’s stated resolution and effective date.

## Q10 — Rate applicable in the past

**Question:** What SmartSaver Tier 1 rate applied on 15 October 2025, and how did it compare with the rate on 15 January 2026?

**Answer** (completed, 10 turns, 40.6s, $0.0873):

I'll help you find the SmartSaver Tier 1 rates for those dates. Let me start by discovering what domains contain this information.The question is about SmartSaver product rates, which appears in `banking.products` and also in `banking.reference` (which has interest rate schedules). Let me bridge the structured store to see if there are tables with rate data:The structured store in `banking` doesn't appear to have a rate table. Let me check the graph in the banking.reference domain which has interest rate schedules:Good! The `banking.reference` domain has interest rate schedules. Let me also check banking.products since SmartSaver is a product:Now let me resolve the SmartSaver product and search for its Tier 1 rates on those specific dates:Perfect! I found the SmartSaver Tier 1 entity. Now let me get the details including its historical rates:The entity history shows rates as of January 15, 2026. I need to find the rate specifically for October 15, 2025. Let me search the interest rate schedules directly:Perfect! I found the information. According to the document, the "Recent Rate History" table in chunk 7 shows the SmartSaver Tier 1 rate for October 15, 2025, and the current rates table shows January 15, 2026. Let me provide you with the answer:

## Answer

Based on the **Interest Rate Schedule 2026** (banking.reference domain), here are the SmartSaver Tier 1 rates:

**15 October 2025:** **4.25% AER**
**15 January 2026:** **4.50% AER**

**Comparison:** The SmartSaver Tier 1 rate increased by **0.25 percentage points** (25 basis points) between October 2025 and January 2026. 

This increase was driven by a rise in the Bank of England base rate from 3.75% (October 15, 2025) to 4.00% (January 15, 2026). FirstUK maintains a fixed 2.5% margin above the BoE base rate, so the rate increases track directly with BoE decisions.

**Grounding:** This information comes from the "Recent Rate History" table and "CURRENT RATES" section in the interest_rate_schedule_2026.md document from the banking.reference domain, which is the official rate schedule document effective January 15, 2026.

**Evaluation comment (grading notes, not shown to artmind):** Tests a date-specific historical lookup rather than a “current rate” answer. The recent-rate history in `reference/interest_rate_schedule_2026.md` contains both dates and should be used to compare them accurately.

## Q11 — Current schedule versus future effective change

**Question:** A rate review happens around the 15th of a month. When would the revised rate normally take effect, and how would customers be notified?

**Answer** (completed, 6 turns, 22.0s, $0.0537):

Rate reviews are likely in the **banking.products** or **banking.policy** domains. Let me search for rate review information.Perfect! I found clear information. Let me get the full context from the top entity match:Based on the artmind data from the **banking.products** domain, specifically the `product_faq.md` document:

**When revised rates take effect:**
Revised rates normally become effective on the **1st of the following month** after the rate review (which occurs around the 15th of the current month).

**How customers are notified:**
Customers are notified by **email** when their interest rate changes.

The document explicitly states: "Rates are reviewed monthly (typically mid-month). If there's a change, it becomes effective on the 1st of the following month. We'll email you if your rate changes."

This timing provides customers with approximately two weeks' notice between the rate review (mid-month) and the effective date (1st of next month).

**Evaluation comment (grading notes, not shown to artmind):** Tests temporal process interpretation from `reference/interest_rate_schedule_2026.md`: distinguish the review date from the effective date, and retrieve the notification channels. It should avoid treating the document’s January current rate as a permanent promise.

## Q12 — Regulatory status changed over time

**Question:** What was FirstUK’s status for enhanced KYC for high-risk customers before 1 March 2026, and what did the Board Risk Committee report later in Q1?

**Answer** (completed, 7 turns, 32.3s, $0.0684):

I'll query the artmind system to find information about FirstUK's KYC status and Board Risk Committee reports.I'll help you find information about FirstUK's enhanced KYC status and Board Risk Committee reports. Let me start by understanding the available domains.Now let me search for FirstUK's KYC status and Board Risk Committee reports. The relevant domains appear to be `banking.risk_governance` (for Board Risk Committee) and `banking.organization` (for FirstUK entity).Let me now search for enhanced KYC information and Board Risk Committee reports for Q1 2026.Perfect! I found the Board Risk Committee Q1 2026 minutes. Now let me search for information about FirstUK's enhanced KYC status before March 1, 2026.Excellent! I now have the information needed to answer both parts of the question. Let me provide the complete answer.

## Answer

**FirstUK's Enhanced KYC Status Before 1 March 2026:**

Before the March 1, 2026 enforcement deadline for FCA-COBS-2026-03 (Enhanced KYC for high-risk customers), FirstUK Bank had **completed implementation** of enhanced KYC procedures. According to the Enhanced KYC Implementation Register (banking.risk_governance domain, dated March 5, 2026):

- All five required implementation elements were completed by late February 2026:
  - Beneficial ownership (>25%) verification via Policy v2 and SOP v3 (completed Feb 20)
  - Enhanced screening for funds above £10,000 via Financial Crime workflow (completed Feb 18)
  - Enhanced screening procedures updated (completed Feb 22)
  - Quarterly review cycle established in KYC system (completed Feb 24)
  - Staff training completed for 84 employees (completed Feb 26)

- Compliance verification: 12 high-risk customer files were sampled on March 4, 2026, with all required evidence confirmed.

**Board Risk Committee Report — Later in Q1 2026:**

The Board Risk Committee met on **March 17, 2026** and reported the following regarding KYC/regulatory compliance:

1. **Regulatory Update on Enhanced KYC:** The Head of Compliance briefed the committee that FirstUK Bank was **compliant** with the FCA-COBS-2026-03 deadline (March 1, 2026), with procedures updated and staff trained.

2. **Other Regulatory Status:** The committee noted that FirstUK Bank was responding well to regulatory changes with no anticipated breaches or compliance issues, and recommended continued proactive monitoring.

3. **Performance Metrics:** The committee reviewed strong fraud detection (97.1% rate) and AML/KYC performance, with 12 high-risk accounts under enhanced monitoring and 3 SARs filed in compliance.

**Grounding:** Both findings are from the banking.risk_governance domain documents: "enhanced_kyc_implementation_register.md" (implementation status) and "board_risk_committee_minutes_q1_2026.md" (Board Risk Committee Q1 report dated March 17–19, 2026).

**Evaluation comment (grading notes, not shown to artmind):** Tests status evolution across linked governance material. Compare `regulations/regulatory_circulars_2026.md`, which records the requirement, deadline, and “under review” status, with `governance/board_risk_committee_minutes_q1_2026.md`, which reports procedures updated and staff trained. Attribute each status to its document/time rather than claiming one timeless status.

## Q25 — Which KYC rule controls after March 2026?

**Question:** An entity customer applied on 10 March 2026, has a beneficial owner with 30% control, and plans to deposit £15,000. Which policy and procedure control, and what must happen before activation?

**Answer** (completed, 11 turns, 60.1s, $0.0912):

I'll help you find which policy and procedure controls apply to this entity customer and what pre-activation requirements are needed. Let me query the artmind system.Now let me search for information on entity customer account opening, beneficial owner verification, and deposit activation requirements. I'll focus on the policy and SOP domains:Let me try a more targeted approach by searching for account opening procedures and KYC requirements:Let me check the superseded version (v3) which is more current:Now let me search for the relevant policies, particularly customer identification and AML:Perfect. Now let me get the full policy_customer_identification_v2 document to understand the complete requirements:Excellent. Let me also check the AML policy for entity customer specifics:Perfect. Now I have the necessary information to provide a comprehensive answer. Let me compile the findings.

---

## Answer

Based on the artmind system, for an entity customer that applied on 10 March 2026 with a beneficial owner holding 30% control and a planned deposit of £15,000, the following policy and procedure controls apply:

### **Controlling Policy & Procedure**

**Policy:** `policy_customer_identification_v2.md` (banking.policy) — effective 2026-03-01, supersedes Version 1.0  
**Procedure:** `sop_account_opening_v3.md` (banking.sop_guides) — effective 2026-03-01

Both documents apply to this application date (10 March 2026, which is after the 1 March 2026 effective date).

### **Why This Customer is High-Risk**

The entity customer triggers **Enhanced Due Diligence (EDD)** requirements because:
1. **Beneficial owner above 25% threshold:** The beneficial owner holds 30% control, exceeding the 25% identification and verification threshold for entity customers (policy_aml.md and policy_customer_identification_v2.md)
2. **Initial deposit exceeds £10,000:** The planned £15,000 deposit triggers source-of-funds verification requirements

### **Pre-Activation Requirements (Step 2: Enhanced Due Diligence Gate)**

Before account activation, the following checks must be completed and documented:

| Check | Evidence Required | Owner |
|---|---|---|
| **Beneficial Ownership** | Identity and ownership/control evidence for the beneficial owner (30% exceeds 25% threshold) | Onboarding Analyst |
| **Source of Funds** | Credible evidence for the £15,000 initial deposit (exceeds £10,000 threshold) | Onboarding Analyst |
| **Enhanced Screening** | OFAC, EU, UN, HMT, PEP, sanctions, and adverse-media screening results | Financial Crime Analyst |
| **Compliance Approval** | Head of Compliance approval before activation; CRO approval if any policy exception is requested | Head of Compliance / CRO |

### **Activation Decision (Step 3)**

- **No material concern:** Head of Compliance approves activation
- **Policy exception requested:** Document rationale and obtain CRO approval
- **Positive screening match, suspicious funds, or unclear ownership:** Refer to Financial Crime — **do not activate** and do not tip off the customer

### **Post-Activation**

After approval and activation:
- Set next KYC review for three months from activation (high-risk customers reviewed quarterly, not annually)
- Retain all evidence in the account record

### **Data Grounding**

This answer is drawn from:
- **policy_customer_identification_v2.md** (banking.policy) — Enhanced Due Diligence table and escalation requirements
- **policy_aml.md** (banking.policy) — Beneficial ownership verification thresholds for non-individual customers (>25%)
- **sop_account_opening_v3.md** (banking.sop_guides) — Procedural steps 1–3 implementing the policy requirements

**Evaluation comment (grading notes, not shown to artmind):** Tests authority and effective-date selection. Use `governance/document_authority_register.md`, `policies/policy_customer_identification_v2.md`, and `sop_procedures/sop_account_opening_v3.md`; both controlled documents apply from 2026-03-01 and supersede earlier versions.

## Q26 — Prove the KYC change was implemented

**Question:** What evidence shows that FirstUK implemented enhanced KYC by the 1 March 2026 deadline, and what remains to be monitored?

**Answer** (completed, 12 turns, 59.1s, $0.1025):

I'll help you find evidence of FirstUK's enhanced KYC implementation by the 1 March 2026 deadline. Let me start by exploring the available domains and then retrieve the specific information.Now let me search for specific information about FirstUK's enhanced KYC implementation:Let me pull detailed context on the Enhanced KYC Implementation Register:Excellent! Now let me get additional details on the monitoring requirements:Now let me check the training completion details:Let me search for training completion information more directly:Perfect! Now let me get the full training document:Let me adjust the command:Let me check what's been implemented by also reviewing the related control measures:Perfect! Now I have comprehensive information. Let me provide a detailed answer based on the evidence found.

---

## Evidence of FirstUK's Enhanced KYC Implementation by 1 March 2026

Based on the **Enhanced KYC Implementation Register** (banking.risk_governance domain, reported 2026-03-05), FirstUK Bank implemented enhanced Know Your Customer procedures and met the FCA-COBS-2026-03 compliance deadline of 1 March 2026. Here is the evidence:

### **Five Core Requirements — All Completed with Evidence**

| Requirement | Evidence | Completion Date | Assurance |
|---|---|---|---|
| **Beneficial Ownership (>25% threshold)** | Policy v2 and SOP v3 gate controls | 2026-02-20 | Sampled |
| **Source of Funds (>£10,000)** | Form and checklist deployed | 2026-02-22 | Sampled |
| **Enhanced Screening Workflow** | Financial Crime workflow implemented | 2026-02-18 | Sampled |
| **Quarterly Monitoring System** | KYC system rule deployed by CTO | 2026-02-24 | First cycle pending |
| **Staff Training** | 84 staff completed across all departments | 2026-02-26 | Complete |

**Source:** enhanced_kyc_implementation_register.md (banking.risk_governance)

### **Staff Training — 100% Completion**
Per the **Enhanced KYC Training Completion Record** (banking.communications, Document ID TRAIN-KYC-2026-03, dated 2026-02-26):
- **Digital Onboarding:** 18/18 complete
- **Branch Account Opening:** 42/42 complete
- **Compliance and Financial Crime:** 16/16 complete
- **Quality Assurance:** 8/8 complete
- **Total:** 84/84 staff (100% completion)

### **Compliance Sampling — Verification Completed**
Compliance sampled 12 high-risk customer files on 2026-03-04 and confirmed all required evidence was present in each file.

---

## What Remains to be Monitored

### **1. Quarterly KYC Monitoring Cycle (Status: First Cycle Pending)**
- **Implementation:** System rule deployed 2026-02-24 by CTO
- **Frequency:** Quarterly reviews required for high-risk customers
- **First Report Due:** 2026-06-30 to Board Risk Committee
- **Monitor:** Ongoing quarterly compliance tracking and effectiveness of enhanced monitoring controls

### **2. Ongoing Assurance Requirements**
- **Beneficial Ownership Verification Control** — Monitor compliance with >25% ownership identification policy gate
- **Enhanced Screening Workflow** — Ensure Financial Crime workflow continues to flag high-risk customers across multiple databases (OFAC, EU, UN, HMT, PEP lists)
- **Source of Funds Verification** — Track form completion and checklist adherence for deposits >£10,000

### **3. Policy and SOP Monitoring**
- **Policy v2 Effectiveness** — New Customer Identification Policy v2 (effective 2026-03-01, Document ID CID-POL-001) requires annual review by 2027-03-01
- **SOP v3 Compliance** — Updated Account Opening procedures (SOP v3) require monitoring of gate effectiveness

### **4. Risk Metrics Governance**
- **Board Risk Committee Oversight** — First quarterly KYC monitoring report due 2026-06-30, with ongoing quarterly submissions thereafter
- **Training Maintenance** — Periodic refresher training required to ensure staff competence as procedures evolve

---

**Grounding:** This answer is based on the Enhanced KYC Implementation Register (Document ID KYC-IMPL-2026-01), Enhanced KYC Training Completion Record (TRAIN-KYC-2026-03), and related regulatory circulars and policy documents in the banking.risk_governance and banking.communications domains.

**Evaluation comment (grading notes, not shown to artmind):** Tests regulatory traceability across `regulations/regulatory_circulars_2026.md`, `risk_compliance/enhanced_kyc_implementation_register.md`, `training/enhanced_kyc_training_completion_2026.md`, and the new policy/SOP. Separate completed implementation from the first quarterly monitoring cycle.

## Q27 — Select the correct monthly rate schedule

**Question:** Which SmartSaver Tier 1 rate applied on 20 February 2026 and on 20 March 2026? Why should the January schedule not be used?

**Answer** (completed, 9 turns, 35.8s, $0.0819):

The question is about SmartSaver Tier 1 rates for specific dates in February and March 2026, and why the January schedule should not be used. This is clearly in the **banking.products** and **banking.reference** domains, where product pricing and interest rate schedules are stored. Let me query those domains.Let me correct that - `entity-resolve` is a top-level query command, not under `graph`:Good. I found several rate-related entities. The most relevant one seems to be the `SmartSaver Account Tier 1 Rate` from `banking.reference` (score 0.898) which mentions "Effective from 2026-03-01". Let me also check the `banking.reference` domain for interest rate schedules that would have rates for February and March 2026:Excellent! I found the February 2026 rate: **4.40% AER for Tier 1 (£0–£10,000)**. Now let me get the March 2026 schedule:Perfect! I found the March 2026 rate: **4.30% AER for Tier 1 (£0–£10,000)**. Now let me check the January schedule to understand why it should not be used:Perfect! I now have all the information needed to answer the question. Let me compile the answer based on the grounded data.

## Answer

**SmartSaver Tier 1 rates applied:**
- **20 February 2026: 4.40% AER** — per *interest_rate_schedule_2026_02.md* (banking.reference domain)
- **20 March 2026: 4.30% AER** — per *interest_rate_schedule_2026_03.md* (banking.reference domain)

**Why the January schedule should not be used:**

The January schedule (interest_rate_schedule_2026.md, effective 2026-01-15) shows a Tier 1 rate of **4.50%** and is explicitly marked **"Superseded Historical Schedule"** with the note: *"Superseded By [[interest_rate_schedule_2026_02]] from 2026-02-01."*

The January schedule should not be used for February 20 and March 20 because:
1. **It was superseded on 1 February 2026** — the February schedule became effective on that date and explicitly supersedes the January version.
2. **It was further superseded on 1 March 2026** — the March schedule supersedes the February schedule from that date onward.

Using an outdated schedule would apply incorrect historical rates to current transactions. The February schedule applies through 28 February 2026, and the March schedule applies from 1 March 2026 onward.

**Grounded in:** banking.reference domain (interest_rate_schedule_2026.md, interest_rate_schedule_2026_02.md, interest_rate_schedule_2026_03.md)

**Evaluation comment (grading notes, not shown to artmind):** Tests explicit supersession. `reference/interest_rate_schedule_2026_02.md` controls February and is superseded by `reference/interest_rate_schedule_2026_03.md` from 2026-03-01.

## Q13 — Resolve a historical internal inconsistency responsibly

**Question:** I found two different approval boundaries for the same complaint compensation decision in the old policy. Which one should I follow?

**Answer** (completed, 5 turns, 20.8s, $0.0506):

I'll help you find and resolve the conflicting approval boundaries in the complaint compensation policy. Let me search the artmind domain for this information.The relevant domains are `banking.policy` (which has complaint policy documents) and `banking.sop_guides` (which has the escalation matrix and complaint resolution guidance). Let me search for approval boundaries related to complaint compensation:Perfect — I found the discrepancy. You have two conflicting approval boundaries for complaint compensation in different versions of the policy:

**The conflict:**

| Boundary | Source | Policy Version |
|---|---|---|
| <£100: Customer Service Manager approval<br/>£100–£500: Director approval<br/>>£500: CEO approval<br/>>£1,000: Board notification + CEO approval | **policy_complaints.md** (banking.policy) | Older version |
| <£500: Complaints Manager approval<br/>£500–£2,000: Director approval<br/>>£2,000: CEO approval<br/>>£5,000: Board notification + CEO approval | **policy_complaints_v3.md** (banking.policy) | Newer version |

**Which one to follow:**

**Follow the boundaries in `policy_complaints_v3.md`** — it is the newer version (as indicated by the `_v3` suffix). The older `policy_complaints.md` has been superseded by the v3 update with revised approval thresholds.

The key changes are:
- **Lower threshold increased** from <£100 (old) to <£500 (new)
- **Mid-tier ceiling increased** from £500 to £2,000
- **Upper CEO approval changed** from >£500 to >£2,000
- **Board notification raised** from >£1,000 to >£5,000

Use **v3 boundaries** for all new compensation decisions.

**Evaluation comment (grading notes, not shown to artmind):** Tests conflict detection. The response should identify the inconsistency in `policies/policy_complaints.md` (v2) and its correction in `policies/policy_complaints_v3.md`, then ask for the decision date or apply the current v3 policy where the decision is current. It must not conceal the conflict.

## Q14 — Erasure request with an AML hold

**Question:** A customer asks us to delete all their data, but their account is subject to an AML-related regulatory hold. What can we delete, what must we retain, and how should we respond?

**Answer** (completed, 7 turns, 34.0s, $0.0657):

The relevant domains are **banking.policy** (for AML policy and data retention rules) and **banking.sop_guides** (for operational procedures on regulatory holds). Let me retrieve the specific guidance.Let me also check the retention policy and search for specific guidance on data deletion during regulatory holds:Now let me search for specific procedures on how to handle customer communications in this situation:Based on the **banking.policy** and **banking.sop_guides** domains, here is the guidance for handling a customer data deletion request under an AML regulatory hold:

---

## **What Data MUST Be Retained**

**Under AML and regulatory holds, you cannot delete:**
- Customer verification documents (5 years post-closure minimum)
- SAR (Suspicious Activity Report) files (5 years)
- Transaction records related to SARs (5 years)
- Risk assessments (5 years)
- Screening results (including AML screening; 5 years)
- KYC/Know-Your-Customer records (5-6 years post-closure)
- Any data subject to a legal hold marked "Do Not Destroy"

**Legal basis:** Money Laundering Regulations 2017, POCA 2002, FCA COBS Part 10, and AML regulatory obligations supersede GDPR right to erasure under UK law.

---

## **What Data MAY Be Deleted**

If no regulatory hold applies to specific non-AML data categories, you may delete:
- Marketing and consent records (if withdrawal given)
- Non-essential operational data not subject to the hold
- Any data no longer necessary for the account's stated purpose

However, this is **secondary** — the regulatory hold typically covers comprehensive account data during an active investigation.

---

## **How to Respond to the Customer**

**Per compliance_training_manual.md and policy_retention.md:**

1. **Acknowledge the request in writing** within 30 days (GDPR timeline)
2. **Explain clearly:**
   - "We are unable to delete your data because your account is subject to an **Anti-Money Laundering (AML) regulatory hold** under the Money Laundering Regulations 2017."
   - State the legal obligation to retain the data
   - Provide the **minimum retention period** (typically 5 years from the hold date or 5 years post-closure, whichever is later)

3. **Offer transparency:**
   - Offer a Subject Access Request (SAR) so customer can see what data is held
   - Explain that restricted processing applies — data is used only for legal/compliance purposes, not marketing or other non-essential uses
   - Confirm when the hold will be reviewed (annually minimum per policy)

4. **Route to DPO:** Forward to Data Protection Officer for formal response ensuring legal compliance and tone.

**Documentation:** Log the request, hold status, and response in the audit trail.

---

**Grounding:** policy_retention.md (banking.policy), policy_aml.md (banking.policy), policy_privacy.md (banking.policy), compliance_training_manual.md (banking.communications), policy_customer_identification.md (banking.policy).

**Evaluation comment (grading notes, not shown to artmind):** Tests policy reconciliation. Use `policies/policy_privacy.md` for erasure exceptions and `policies/policy_retention.md` for AML/POCA and regulatory-hold retention. The answer should distinguish the customer right from the legal exception, not issue an unconditional deletion promise.

## Q15 — Account closure while a fraud investigation is open

**Question:** Can we close a customer’s account when there is an outstanding fraud investigation and a complaint about the same transactions?

**Answer** (completed, 11 turns, 54.1s, $0.1075):

The question about closing a customer account with an outstanding fraud investigation and complaint touches policy, procedures, and case handling. I'll search the relevant domains: `banking.policy` (for account closure policy), `banking.sop_guides` (for account closure and fraud investigation procedures), and `banking.cases` (for case-handling context).Let me search for more specific guidance on account closure with fraud and complaints:Let me run the query and then search directly for account closure procedures and fraud investigation policies:Now let me search for fraud investigation and complaint handling policies:Perfect. I now have the key evidence. Let me pull the fraud investigation procedure more fully to check for any additional constraints:Let me search for complaint handling procedures to understand if there are holds during complaint resolution:Based on the evidence from the artmind knowledge graph, here is the answer:

## No — You cannot close a customer's account when there is an outstanding fraud investigation and an active complaint about the same transactions.

**Key prohibition from `sop_account_closure.md` (banking.sop_guides):**

In Step 2: Account Verification, the SOP explicitly states:

> **Verification Checks:**
> - ✅ No active disputes (complaint, chargeback, reversal)
> - ✅ No legal holds (litigation, regulatory investigation, tax lien)
> - ✅ **No outstanding fraud investigation**
> 
> **If Issues Found:**
> - Escalate to manager/Compliance Head
> - **Resolve issue before proceeding**
> - Document delay reason in system

**Why this rule exists:**

1. **Legal holds on accounts** — Per `policy_retention.md`, disputes and investigations create obligations to preserve all transaction records and evidence. Closing an account compromises evidence preservation.

2. **Complaint investigation timeline** — The `complaint_resolution_guide.md` requires an 8-day investigation window before resolution. Closing the account mid-investigation blocks access to transaction history needed for investigation and remediation.

3. **Fraud investigation requirements** — The `fraud_investigation_procedure.md` mandates evidence preservation, system-log retention, and transaction-record protection during investigation. Account closure would interrupt this process.

**What must happen first:**

- Complete the fraud investigation (gather evidence, determine unauthorized transactions, issue refund if applicable)
- Complete the complaint investigation and resolution (reach a finding within 8 days)
- Resolve both disputes to resolution
- Then escalate to a manager/Compliance Head for account closure approval

**Source:** `sop_account_closure.md` (Step 2: Account Verification), `fraud_investigation_procedure.md`, `complaint_resolution_guide.md`, and `policy_retention.md` (banking.sop_guides and banking.policy domains).

**Evaluation comment (grading notes, not shown to artmind):** Tests constraints across processes. `sop_procedures/sop_account_closure.md` requires no outstanding fraud investigation or active dispute; `policies/policy_privacy.md`, `policies/policy_retention.md`, `policies/policy_fraud.md`, and `policies/policy_complaints_v3.md` add related treatment and record-retention considerations. The result should explain the blocking conditions and escalation path.

## Q16 — An exception outside risk appetite

**Question:** A business unit wants an exception to a risk limit because of a commercial opportunity. Who needs to be involved and what must be documented before it can proceed?

**Answer** (completed, 12 turns, 50.9s, $0.1001):

I'll search the artmind system for information about risk limit exceptions and required documentation.Now I'll search the risk governance and policy domains for information about risk limit exceptions:Let me search more specifically for risk limit exceptions:I found product exception references. Let me search more specifically for exception handling procedures related to commercial opportunities and risk limits:Perfect! I found the most relevant information. Let me pull the full content from the Risk Appetite Statement document which has the most direct answer:Excellent! Now let me get more details on the governance structure and approvals by checking the Board Risk Committee Charter:Let me correct that - entity-context isn't under `graph`:Let me resolve the entity first:Now let me get the executive committee details for the approval process:Perfect! I have comprehensive information. Let me compile the answer based on the documents I've found:

## Answer

Based on the artmind system (specifically **risk_appetite_statement_2026.md** in the banking.risk_governance domain), when a business unit wants an exception to a risk limit due to a commercial opportunity, the following process must be followed:

### **Who Needs to Be Involved:**

1. **Risk Team** — Flags the issue when the proposed decision conflicts with risk appetite
2. **Business Unit** — Provides the commercial rationale for why the exception is necessary
3. **Executive Committee** — Conducts formal review, specifically:
   - **Chief Risk Officer (CRO)** — Enterprise risk management authority
   - **Chief Financial Officer (CFO)** — Financial and capital management authority
   - **Chief Executive Officer (CEO)** — Chief executive responsibility
4. **Board Risk Committee** — Notified and involved if the exception is deemed significant

### **What Must Be Documented:**

Before the exception can proceed, the following must be documented and recorded:

1. **Business Unit Rationale** — Written explanation of why the exception is necessary, including the commercial opportunity details
2. **Risk Assessment** — How the exception impacts risk appetite and risk metrics
3. **Decision and Justification** — The documented decision made by the Executive Committee with full justification recorded
4. **Escalation Documentation** — If the exception is significant, Board Risk Committee notification and their review

**Source:** *risk_appetite_statement_2026.md* (banking.risk_governance domain), Section "Escalation & Exception Handling"

The governance structure emphasizes that exceptions conflicting with stated risk appetite require senior executive and board-level oversight, with all decisions formally documented for audit and regulatory purposes.

**Evaluation comment (grading notes, not shown to artmind):** Tests exception governance. Trace `risk_compliance/risk_appetite_statement_2026.md` through its escalation-and-exception process, then connect it to `sop_procedures/sop_exception_handling.md`, `guides/escalation_matrix.md`, and the Board Risk Committee’s remit in `governance/board_risk_committee_charter.md` as appropriate to the threshold and materiality.

## Q28 — Report the case impact without hiding the difference

**Question:** How many customers were affected by CASE-2026-041? Give the answer suitable for a Board risk report.

**Answer** (completed, 7 turns, 27.7s, $0.0594):

The case reference `CASE-2026-041` appears in the `banking.cases` domain. Let me retrieve the detailed information about this case and its customer impact.Now let me search for the case entity and its impact assessment:I found multiple case entities related to CASE-2026-041. The most relevant for customer impact is the "CASE-2026-041 — Customer Data Exposure" which identifies **118 confirmed affected customers**. Let me retrieve the full details to confirm this number and provide context suitable for a Board risk report:---

## Answer: Customer Impact from CASE-2026-041

**Confirmed affected customers: 118**  
**(Discrepancy note: Initial count of 120 remains unresolved)**

### Board-Suitable Summary

CASE-2026-041 involved a customer-document export configuration defect that exposed statement PDFs outside the intended account scope. The incident was detected on 2026-07-02 and the affected export service was immediately disabled.

**Impact metrics:**
- **Security's initial assessment:** 120 potentially affected customers
- **Operations' reconciliation count:** 118 confirmed affected customers (after removing 2 failed-download events)
- **Status:** Forensic reconciliation pending — the discrepancy between the two counts remains unresolved

**Key governance:** The case remains open with active workstream ownership across: CTO (containment), Customer Service (complaints), DPO (privacy and holds), Financial Crime (AML records), and CRO (governance reporting).

**Source:** banking.cases domain — case_2026_041_overview.md (opened 2026-07-02, current status as documented).

**Evaluation comment (grading notes, not shown to artmind):** Tests an intentional unresolved conflict. `cases/case_2026_041_overview.md` and `cases/case_2026_041_incident_timeline.md` distinguish 120 potentially affected customers from 118 confirmed customers. Attribute both; do not collapse them into a final total.

## Q29 — Erasure request during the open case

**Question:** A CASE-2026-041 customer wants all data erased now. What can we action, what must remain, and when is the decision reviewed?

**Answer** (completed, 8 turns, 32.0s, $0.0676):

I'll search the artmind system for information about this customer case and data deletion policies.I'll search the artmind system for case CASE-2026-041 and its data deletion requirements.I found the relevant domain. Now let me retrieve the case information and retention decision details.Now let me get more detail on the case overview and retention policy context.Let me resolve the entity first.Now let me get the full context on the primary case entity.Perfect. I have the complete information needed. Let me provide the answer based on the retention decision for CASE-2026-041.

---

## CASE-2026-041: Data Deletion Decision Summary

**Grounded in:** `banking.cases` domain — specifically `case_2026_041_retention_decision.md` (Decision Date: 2026-07-07, Owner: Data Protection Officer).

### What CAN Be Actioned Now

- **Marketing preferences:** Suppress immediately if the customer requests it.
- **Unneeded duplicate copies:** Restrict and review for deletion (a staged process—not immediate erasure, but flagged for removal after review).

### What MUST Remain (Cannot Erase)

- **Incident, complaint, and notification records:** Retain and restrict access
- **AML/KYC and transaction evidence:** Retain under regulatory obligation (financial crime / anti-money laundering regulatory hold)
- **Subject access material:** Must be provided to the customer electronically after identity verification

### Review Timeline

**Hold Review Date:** 2026-10-07

The case is under **active legal and regulatory hold**. The decision document explicitly states: "Review the hold on 2026-10-07 and after all matters close." This is a conditional hold—erasure is not denied permanently, but deferred pending:
1. Closure of the open incident
2. Resolution of the customer complaint
3. Completion of the regulatory assessment
4. All related matters being fully concluded

**Key communication note:** The Data Protection Officer is instructed to "explain the hold and review point without saying erasure is permanently denied"—framing this as a temporary hold with a scheduled review, not a permanent refusal.

**Evaluation comment (grading notes, not shown to artmind):** Tests case-specific privacy and retention reasoning using `cases/case_2026_041_retention_decision.md`, `policies/policy_privacy.md`, `policies/policy_retention.md`, and `policies/policy_aml.md`.

## Q17 — Open Banking access, consent, and retention

**Question:** What must the bank provide for Open Banking, which customer data can be shared, how is consent handled, and how long can the data be held?

**Answer** (completed, 7 turns, 32.6s, $0.0734):

The question addresses Open Banking requirements across multiple dimensions. I can see a relevant document in `banking.reference`: "open_banking_delivery_outcome_2026_07.md". Let me retrieve this and related policy domains:Good start. Now let me check the privacy policy for data handling and retention rules:Now let me search for more specific Open Banking consent and data-sharing requirements:Perfect. I now have comprehensive information about Open Banking requirements. Let me provide the answer:

---

## Open Banking Requirements

**Sources:** open_banking_delivery_outcome_2026_07.md (banking.reference) and policy_privacy.md (banking.policy)

### What the Bank Must Provide

FirstUK Bank must provide **OAuth 2.0 APIs** that expose:
- **Account data** — customer account information
- **Transaction history** — detailed transaction records
- **Standing orders** — recurring payment instructions
- **Direct debit mandates** — recurring payment authorizations

### Customer Data That Can Be Shared

The bank shares the following data via Open Banking APIs, subject to customer consent:
- Account information
- Transaction records
- Standing order details
- Direct debit mandate information
- Consent scope, expiry, and revocation status

### How Consent Is Handled

Consent management includes:
1. **Explicit consent recording** — the bank records each customer's specific consent
2. **Scope management** — consent specifies exactly which data can be shared
3. **Expiry tracking** — the bank records consent expiration dates
4. **Revocation capability** — customers can withdraw consent, which is recorded in the system
5. **Transparent communication** — privacy notices explain data-sharing purposes, lawful basis, and retention periods

### Data Retention Period

API-accessed data is **purged after 90 days** unless the customer renews their consent. After 90 days, without active renewal, the bank deletes the data that was accessed via the Open Banking API.

Additionally, underlying customer data (accounts, transactions) follows standard retention schedules: **6 years minimum** for transaction records and customer profiles, with KYC/AML records retained for **5–6 years post-closure**.

**Status note:** FirstUK completed core Open Banking delivery on 2026-06-30 per FCA-OP-2026-01; 37 legacy direct debit mandates are under remediation (migration target: 2026-08-15).

**Evaluation comment (grading notes, not shown to artmind):** Tests a regulatory-to-system-to-privacy chain. Use the API scope, OAuth, consent, and 90-day condition in `regulations/regulatory_circulars_2026.md`; link to relevant systems in `organization/systems.md` and data protection principles in `policies/policy_privacy.md`.

## Q18 — A payment outage causing customer harm

**Question:** Payment processing is unavailable and customers are missing scheduled payments. Give the immediate operational response, severity/escalation considerations, customer communication, and the risk/governance follow-up.

**Answer** (completed, 10 turns, 63.2s, $0.1196):

I'll use the artmind-query skill to search for documented incident response procedures and payment processing protocols.Routing shows relevant domains across incident response, SOPs, policy, and communications. I'll now search for payment processing incident procedures and operational guidance.Good—I can see incident_response_plan.md exists. Let me retrieve it and search for payment-processing-specific incident procedures and customer communication protocols.Now let me search for more specific guidance on payment systems, customer communication templates, and operational risk policies.Now let me retrieve the escalation matrix and communications templates for payment incident guidance.Now let me fetch customer communication templates and operational risk policy for payment system context.Let me pull in operational risk and policy guidance on financial impact and governance requirements for system failures.Based on FirstUK Bank's documented procedures, here's the comprehensive operational response to payment processing unavailability with missed scheduled payments:

---

## **IMMEDIATE OPERATIONAL RESPONSE**

**Phase 1: Detect & Declare (0–15 minutes)**
- **Verify incident** — confirm payment processing system is down, not a regional outage
- **Activate incident response team** — call IT On-Call (0800-888-TECH) + CTO + Operations Lead immediately
- **Assign Incident Commander** — escalate to CISO or CTO to lead response
- **Classify severity** — per *incident_response_plan.md* (banking.reference): **Severity 1 (Critical)** if >4 hours outage or >1,000 customers affected; **Severity 2 (High)** if 1–4 hours
- **Activate war room** — real-time Slack channel + video conference for hourly updates

**Phase 2: Containment (15–60 minutes)**
- **Isolate failed component** — disconnect from network if malware suspected; preserve evidence
- **Failover to backup** — routing to secondary payment processor if available
- **Freeze new payment submissions** — stop accepting new scheduled payments to prevent data loss
- **Begin restoration** — apply patches, rebuild from clean backup, test before restoration

**Phase 3: Assess Customer Impact**
- Query affected customer count and payment amounts
- Categorize failures: scheduled bills (standing orders), direct debits, transfers
- Estimate financial impact and determine if regulatory notification required

---

## **SEVERITY & ESCALATION**

Per *escalation_matrix.md* (banking.sop_guides):

| **Trigger** | **Escalation Level** | **Timeline** | **Who** |
|---|---|---|---|
| **System outage >4 hours** | Level 4–5 | Within 4 hours | CTO + CEO + Board |
| **Customers missing payments** | Level 4–5 | Immediate | CRO + CEO + Compliance |
| **Regulatory breach** | Level 4–5 | Within 24 hours | FCA notification may be required |
| **Financial impact >£100k** | Board notification | Within 2 hours | CEO + Board Risk Committee |

**Executive Notification Timeline:**
- **Severity 1/2:** Executive team (ASAP), Board within 2 hours, CRO within 1 hour
- **Call on-call escalation:** CRO Mobile (if >4 hours), CEO/COO if systemic
- **Incident escalation matrix:** IT Operations → CTO → CEO (if >4 hours)

---

## **CUSTOMER COMMUNICATION**

**Immediate (0–2 hours):**
- **SMS/push notification** (if channel available):  
  "FirstUK: Payment processing temporary unavailable. Scheduled payments delayed. Updates at firstuk.bank. Support: 0800-555-2265"
- **Website banner** — explain outage, expected restoration time, customer action (none required)
- **Social media** — brief acknowledgment, route complaints to support line
- **Call centre script** (per *call_centre_script.md*): "Your payment couldn't go through due to system maintenance. We're fixing it and will process your payment automatically. You won't be charged late fees."

**Follow-up (4–24 hours):**
- **Email to affected customers** — per *email_templates.md* (banking.communications), include:
  - Timeline of outage
  - Which payments were affected
  - When automatic reprocessing will occur
  - No late fees or charges incurred
  - Support contact
  - Apology for inconvenience

**Key Messaging:**
- ✅ **Customers are not liable** for missed payment deadlines during system outage (per operational risk policy)
- ✅ **No late fees** — waive any applied by counterparties
- ✅ **Automatic retry** — payments will process when system restored, without customer action
- ✅ **Support line** — 0800-555-2265 available for questions

---

## **RISK & GOVERNANCE FOLLOW-UP**

**Immediate (Day 1):**
1. **Incident report** to CRO + CEO + Board — include scope, duration, customer count, financial impact, root cause (preliminary)
2. **Regulatory assessment** — Compliance determines if FCA/PRA notification required within 24 hours (per *policy_operational_risk.md*, incidents >£100k or customer data exposure require escalation)
3. **Investigation timeline** — per operational risk policy:
   - **If financial impact >£100k:** Immediate CRO + CEO escalation
   - **If £10k–£100k:** Investigate within 24 hours
   - **If <£10k:** Investigate within 5 business days
4. **Document remediation** — identify root cause, control gaps, prevention measures
5. **Reserve provisions** — estimate customer remediation costs (late-fee waivers, interest adjustments, goodwill credits)

**Governance Reporting (Day 2–7):**
1. **Incident log entry** — per risk policy, categorized as **System Risk** (technology failure, service disruption)
2. **Risk scoring** — assess against operational risk matrix:
   - **Impact:** Depends on customer count/amounts; likely **High (£250k–£1M+)** if widespread
   - **Likelihood:** **Very likely for critical payment system** (recurring risk)
   - **Current risk rating:** Likely **Red (16–25, Critical)** requiring urgent mitigation
3. **Post-incident review** (1–2 weeks):
   - Incident commander leads review with IT, operations, compliance
   - Assess detection speed, response effectiveness, gaps
   - Assign corrective actions (system redundancy, failover testing, monitoring)
   - Owner + timeline (30–90 days to close)
4. **Board Risk Committee notification** (quarterly) — summary of incident, remediation status, systemic risk implications
5. **Internal Audit review** — test control effectiveness, verify corrective actions implemented

**Regulatory/Compliance:**
- **FCA notification:** Required if incident materially affects customer service (system >4 hours typically qualifies for notification within 24 hours per FCA conduct rules)
- **Operational resilience reporting:** Document as part of PRA Pillar 2 operational risk capital requirements
- **Risk appetite assessment:** Payment processing outage breaches conservative risk appetite (<1 major incident/year, <£50k annual operational loss target); escalate to risk committee for appetite review

---

## **SUMMARY**

| **Area** | **Action** |
|---|---|
| **Operational** | Level 4–5 escalation (CTO → CEO/Board); failover + restore; hourly updates |
| **Severity** | Severity 1 (Critical) if >4h; Board notification within 2 hours |
| **Customer** | SMS/push + email within 2 hours; confirm no late fees; automatic retry messaging |
| **Governance** | Incident report day 1; regulatory assessment within 24h; post-review 1–2 weeks; Board reporting quarterly |
| **Risk** | High-to-critical risk rating; control gap assessment; root cause + prevention; audit follow-up |

**Sources:**
- [incident_response_plan.md](banking.reference) — phases, escalation timelines, team composition, severity classification
- [escalation_matrix.md](banking.sop_guides) — operational incident escalation paths, approval authority, response times
- [policy_operational_risk.md](banking.policy) — incident classification, investigation timelines, escalation thresholds, risk governance
- [email_templates.md](banking.communications) — customer incident notification content

**Evaluation comment (grading notes, not shown to artmind):** Tests broad graph traversal. It should connect `reference/technology_production_runbook.md`, the severity and incident process in `reference/incident_response_plan.md`, `guides/escalation_matrix.md`, `policies/policy_complaints_v3.md`, `risk_compliance/risk_appetite_statement_2026.md`, and `governance/board_risk_committee_charter.md`. A strong answer distinguishes live incident actions from later remediation and reporting.

## Q19 — Fraud alert false positives

**Question:** Fraud controls are blocking many legitimate transactions and generating customer complaints. Which teams and systems should investigate, what evidence is needed, and how should affected customers be treated?

**Answer** (completed, 13 turns, 53.3s, $0.1628):

I need to research artmind data to answer your question about fraud controls, customer complaint handling, and investigation procedures. Let me start by exploring the available domains.Excellent — I can see fraud-related content in multiple domains. The question involves fraud controls, customer complaints, and investigation procedures, which map to:
- `banking.policy` (fraud policy)
- `banking.sop_guides` (fraud investigation procedure, complaint resolution, escalation matrix)
- `banking.cases` (actual complaint cases)
- `banking.communications` (customer communication guidance)

Let me retrieve the key procedures and policies:Let me read that full output and also get the complaint resolution and escalation procedures:Now let me get the fraud policy to understand control measures and high-level fraud handling approach:Let me look for information about the organization to understand the teams involved:Let me read that departments file:Perfect. Now I have comprehensive information. Let me provide a complete answer based on the artmind data.

---

## Answer

Based on FirstUK Bank's documented procedures and policies (domains: banking.sop_guides, banking.policy, banking.organization), here's the comprehensive guidance for fraud controls generating customer complaints:

### **Teams That Should Investigate**

**Primary Investigation Team** (fraud_investigation_procedure.md: Chunk 4, 5):
- **Financial Crime Team** (Head of Financial Crime, CRO reporting line) — lead investigator
- **Risk Management** — fraud trend analysis
- **Compliance Department** — regulatory implications
- **Technology team** — system logs, fraud detection engine review

**Secondary Support** (escalation_matrix.md: Chunk 9):
- For fraud >£1,000–£10,000: Financial Crime Team + CRO
- For fraud >£10,000: CRO + CEO + potential Board escalation
- **Customer Service** — customer communication and retention

### **Systems That Need Review**

1. **Fraud Detection Engine (FDE)** — rule tuning for false positive reduction
2. **Account Management System (AMS)** — transaction history and patterns
3. **Authentication Systems** — verify customer identity and access logs
4. **Payment Processing System** — confirm transaction status and settlement

### **Critical Evidence to Gather** (fraud_investigation_procedure.md: Chunk 6, 7)

**Transaction Evidence:**
- Original transaction records and screenshots
- Account statements (30 days before fraud date)
- System logs (login history, IP addresses, timestamps)
- Device information and geo-location data
- Complete timeline of disputed transactions

**Customer Evidence:**
- Customer interview notes (recorded if possible)
- Statement of authorization/lack thereof
- Security questions and authentication history
- Communication records (emails, chat logs, call notes)

**Litigation-Ready Documentation:**
- Evidence preserved in locked secure storage
- Separate from normal account files
- Retention: Minimum 7 years
- Chain of custody maintained

**Fraud Score Assessment** (fraud_investigation_procedure.md: Chunk 9):
- Score each piece of evidence (0–12 scale)
- 10–12 = Confirmed fraud → Refund + Investigation
- 7–9 = Fraud likely → Refund + Investigation
- 4–6 = Fraud possible → Further investigation or conditional refund

### **How Affected Customers Should Be Treated** (complaint_resolution_guide.md, policy_fraud.md)

**Immediate Actions (Urgent Cases):**
- Freeze customer account within 5 minutes
- Block affected debit/credit cards
- Reset online banking password
- Log customer out of all sessions
- Contact customer by phone (if possible)
- Provide **temporary access** (alternate card, temporary funds)

**Timeline & Communication:**
- **8-day response guarantee** (complaint_resolution_guide.md: Chunk 4)
- Acknowledgment within 1 business day with complaint reference number
- Investigation completion within 24–48 hours (fraud_investigation_procedure.md: Chunk 5)
- Final decision by day 8

**Refund & Compensation** (policy_fraud.md: Chunk 12, 13)

**If Fraud Confirmed:**
- Full reimbursement within 10 business days (Direct Debit Guarantee)
- Includes daily interest accrued
- Payment placed back to original account
- No customer liability (if reported within 24 hours)

**If Fraud Likely (Balance of Evidence):**
- Issue refund (same process)
- Treat as potential fraud for prevention

**Compensation Scale** (complaint_resolution_guide.md: Chunk 22):
- Minor issues: £20–£50
- Moderate issues (<£100 loss): £50–£150
- Significant issues (£100–£500): £150–£300
- Major issues (>£500): £300+ per FCA guidance

**Prevention Actions:**
- New card issued (old one cancelled)
- Password reset forced
- 2FA enabled (if not active)
- Account flagged for enhanced monitoring
- Identity theft protection offered (account takeover cases)
- Review standing orders and direct debits for compromise
- Customer education on fraud prevention

**Escalation & Ombudsman Rights** (complaint_resolution_guide.md: Chunk 18–20)

Customers can escalate to **Financial Ombudsman Service** if:
- Unhappy with FirstUK's decision
- No response within 8 days
- No acknowledgment received

**Ombudsman Timeline:** Typically 3–6 months (free and independent)

### **Regulatory Obligations** (escalation_matrix.md: Chunk 9)

For fraud >£1,000:
- Law enforcement notification required
- Police report filed (reference number documented)
- Evidence preserved for police investigation

For >£10,000:
- Board notification within 4 hours
- FCA notification if required
- Potential SAR (Suspicious Activity Report) filing

### **Customer Service Standards**

**8-Week Maximum Complaint Window** (complaint_resolution_guide.md: Chunk 23):
- No retaliation for complaining
- Free escalation to ombudsman
- 6-month window to escalate after bank's final response

**Multi-Channel Support** (complaint_resolution_guide.md: Chunk 5–8):
- Phone: 0800-555-2265 (Mon–Fri 08:00–20:00, Sat 09:00–17:00)
- Email: support@firstuk.bank
- Online complaint form: www.firstuk.bank/complaints
- Branch visit available

---

**Sources:**
- fraud_investigation_procedure.md (banking.sop_guides domain)
- complaint_resolution_guide.md (banking.sop_guides domain)
- escalation_matrix.md (banking.sop_guides domain)
- policy_fraud.md (banking.policy domain)
- departments.md (banking.organization domain)

**Evaluation comment (grading notes, not shown to artmind):** Tests incident, system, fraud, and customer-service connections. The technology runbook explicitly describes a high fraud-alert-rate issue; combine it with `policies/policy_fraud.md`, `guides/fraud_investigation_procedure.md`, `reference/technology_application_landscape.md`, `policies/policy_complaints_v3.md`, and `organization/departments.md`.

## Q20 — Audit finding to remediation status

**Question:** The Q4 2025 audit found weaknesses in standing-order audit trails and account-closure documentation. What were the required actions, who should own them, and what evidence would demonstrate closure?

**Answer** (completed, 6 turns, 27.1s, $0.0644):

The question references the Q4 2025 audit, which appears in the `banking.risk_governance` domain. Let me retrieve the audit report and related governance documents:Now let me search for audit findings related to standing orders and account closure:Perfect. I have found the audit report with the specific findings. Let me extract the key details about the two weaknesses and format the answer clearly.

## Answer

Based on the Q4 2025 audit report (audit_report_q4_2025.md) from **banking.risk_governance** domain, the audit identified **two high-priority weaknesses** in standing-order audit trails and account-closure documentation:

### **Required Actions**

#### **1. Standing Order Audit Trail (Medium Priority)**
**Weakness:** 3 of 20 standing orders (15%) had modifications without documented audit trail—e.g., payment amount changes not logged.
- **Required action:** Log all modifications with timestamp and user identity
- **System change:** Enhancement to standing-order system required
- **Completion target:** March 15, 2026
- **Owner:** CTO

**Additional finding:** 2 of 20 orders (10%) had cancellation delays >1 day (SLA is immediate).
- **Required action:** Automate cancellation processing
- **Completion target:** February 15, 2026

#### **2. Account Closure Documentation (High Priority)**
**Weakness:** 3 of 15 closures tested (20%) were bank-initiated but only 1 had proper 30-day notice letter on file; only 2 of 3 had documented reason.
- **Required actions:**
  1. Implement mandatory closure letter procedure
  2. Document closure reason (dropdown: regulatory, fraud, policy, other)
  3. Enforce mandatory 30-day notice for bank-initiated closures
  4. Maintain audit trail for all closures
- **Completion target:** February 15, 2026
- **Owner:** Head of Operations

---

### **Evidence of Closure**

The audit report specifies retest points that would demonstrate remediation:

1. **Standing Orders:** Q1 2026 retest showing 100% of modifications logged with audit trail; cancellations processed within SLA (immediate)
2. **Account Closures:** Q1 2026 retest showing:
   - 100% of bank-initiated closures have 30-day notice letter on file
   - 100% of closures have documented closure reason
   - 100% audit trail completion across all closure types

**Grounding:** audit_report_q4_2025.md, sections D (Standing Orders Management) and E (Account Closure), **banking.risk_governance** domain.

**Evaluation comment (grading notes, not shown to artmind):** Tests finding-to-control-to-owner reasoning. Start with the required actions and deadlines in `risk_compliance/audit_report_q4_2025.md`; connect the relevant SOPs, Operations ownership in `organization/departments.md`, and the Board Risk Committee’s responsibility to monitor audit findings and remediation.

## Q21 — KYC exception through onboarding, compliance, and audit

**Question:** An online account was opened without identity verification. What is the immediate remediation, who approves any exception, and which control bodies need visibility?

**Answer** (completed, 12 turns, 68.3s, $0.0965):

This question is about account opening without identity verification — a compliance and procedural matter. The relevant domains are **banking.policy** (policies governing KYC), **banking.sop_guides** (procedural remediation steps), and **banking.risk_governance** (control bodies and exceptions). Let me query these domains.Now let me search for the specific KYC and account opening policies and procedures related to remediation and exceptions:Let me try a more targeted search focusing on account opening SOP and exception handling:Good start. Now let me search for exception approval authority and control body visibility requirements:Now let me get the specific policy on KYC and account opening requirements:Let me now fetch the detailed policy on KYC requirements and the escalation matrix for regulatory breaches:Perfect. Now let me get more detail on the escalation matrix for regulatory breaches:Excellent. Now let me confirm the specific control bodies and risk governance escalation:Perfect. Now I have comprehensive coverage. Let me compile the answer based on the sourced documents.

---

## Answer

Based on the artmind documents, here is the required response to an account opened without identity verification:

### **Immediate Remediation**

**Direct action** (sop_exception_handling.md, banking.sop_guides):
1. **Contact the customer immediately** via email requesting identity documents
2. **Restrict the account** if the customer cannot provide the required document
3. **SLA: 5 business days** to provide documentation before account freeze

**Parallel investigation** (escalation_matrix.md, banking.sop_guides):
- Document the KYC file as missing
- Note the account opening method (e.g., online)
- Escalate to Compliance for investigation

---

### **Who Approves Any Exception**

**Compliance Head** has sole authority for exceptions to standard KYC requirements (policy_customer_identification.md, banking.policy):
- Written approval required and recorded in the system
- Exception rationale must be documented
- Applies to scenarios where customer cannot provide standard documents (refugee, foreign customer, complex structure, or PEP status)

**For regulatory breaches**, escalation depends on severity (escalation_matrix.md, banking.sop_guides):
- **Low-impact regulatory breach** → **CRO + Compliance** approve remediation (5-day timeline)
- **Significant regulatory breach** → **CRO + CEO + Legal** with FCA/PRA notification (24-hour timeline)

---

### **Which Control Bodies Need Visibility**

**By severity level** (escalation_matrix.md & board_risk_committee_charter.md):

1. **Standard KYC exceptions** → **Compliance Department** (routine AML/KYC approvals are Executive Authority per charter)

2. **Regulatory Rule Breach (Low Impact)** → **Chief Risk Officer (CRO)** + internal notification documented

3. **Significant Regulatory Breach** → 
   - **Board Risk Committee** — immediate notification (quarterly body; regulatory engagement is core responsibility)
   - **Board of Directors** — immediate notification
   - **FCA/PRA** — regulatory notification per rules
   - **Legal team** — involved in remediation planning

**Grounded in**: sop_exception_handling.md, escalation_matrix.md, board_risk_committee_charter.md, policy_customer_identification.md — all from banking.sop_guides, banking.policy, and banking.risk_governance domains.

**Evaluation comment (grading notes, not shown to artmind):** Tests an audit-like case across several layers. `sop_procedures/sop_exception_handling.md` gives the concrete scenario; connect it with `sop_procedures/sop_account_opening.md`, `sop_procedures/sop_kyc_verification.md`, `policies/policy_customer_identification.md`, `policies/policy_aml.md`, `regulations/regulatory_circulars_2026.md`, and `governance/internal_audit_charter.md`. The answer must not normalize a policy exception as routine approval.

## Q22 — Prioritize control improvements

**Question:** Which controls should Internal Audit prioritise in 2026 if it wants to reduce the most material customer, compliance, payment, fraud, and technology risks?

**Answer** (completed, 12 turns, 58.2s, $0.1195):

I'll help you find which controls Internal Audit should prioritise in 2026 to reduce the most material risks. Let me query the artmind system to find this information.Now let me search for information about Internal Audit priorities and controls related to the risk categories you mentioned:Let me search more specifically for Internal Audit priorities and control measures:Excellent! I found the 2026 audit plan. Now let me get more details about the specific audit priorities and their associated risks:Perfect! Now let me search for more details about the risks associated with each audit and what controls are most critical:Let me search for information about the risks and controls in the risk governance domain:Now let me search for control measures specifically related to the audit priorities:Let me pull the actual document text to give you the complete picture of audit priorities and control recommendations:Now let me search for more information on the risk categories mentioned in your question:Perfect! I now have comprehensive information to answer the question. Let me compile the answer based on the artmind data.

---

## Internal Audit Controls Priorities for 2026

Based on the **2026 Internal Audit Plan** (from Board Risk Committee Minutes Q1 2026, `banking.risk_governance` domain), Internal Audit should prioritise the following **five controls** to reduce the most material customer, compliance, payment, fraud, and technology risks:

### **Approved 2026 Audit Priorities by Risk Rating:**

| **Priority** | **Audit Area** | **Risk Rating** | **Quarter** | **Hours** | **Risk Categories Addressed** |
|---|---|---|---|---|---|
| **1** | **Account Opening & KYC** | High | Q2 | 40 | Compliance, Customer, Technology |
| **2** | **Payment Processing** | High | Q3 | 35 | Payment, Compliance, Fraud |
| **3** | **Fraud Detection Systems** | High | Q2 | 30 | Fraud, Technology, Customer |
| 4 | IT General Controls | Medium | Q4 | 30 | Technology |
| 5 | Operational Risk Management | Medium | Q1 | 25 | Compliance, Operational |

**Total Effort: 160 hours (within budget)**

---

### **Why These Controls Matter for Each Risk Category:**

**Compliance Risk:**
- Enhanced KYC for high-risk customers (FCA-COBS-2026-03, effective March 1, 2026)
- Beneficial ownership identification and source-of-funds verification
- AML audit trail integrity and 5-year record retention requirements

**Customer Risk:**
- Account opening process integrity (40 hours devoted to high-risk audit)
- Know-Your-Customer controls prevent customer friction from false positives
- Complaint handling and customer reimbursement protections

**Payment Risk:**
- Payment Processing controls audited with 35 hours (high-risk designation)
- Transaction monitoring and rule-based detection systems
- Segregation of duties and authorization controls

**Fraud Risk:**
- Fraud Detection Systems audited with 30 hours (high-risk)
- Real-time detection engine effectiveness (current 97.1% detection rate)
- Investigation procedures and false positive management

**Technology Risk:**
- IT General Controls audited in Q4 2026 (30 hours)
- API Gateway (Kong) authentication, rate limiting, and input validation
- Database encryption (AES-256), backup/disaster recovery (RTO 2 hours, RPO 15 minutes)
- Access controls and change management

---

### **Grounding:**

**Sources:** 
- **Document:** `board_risk_committee_minutes_q1_2026.md` (Section 5: Internal Audit Plan 2026)
- **Domain:** `banking.risk_governance`
- **Related Documents:** `internal_audit_charter.md`, `risk_appetite_statement_2026.md`, `compliance_bulletins_2026.md`

**Board Decision:** Unanimously approved by the Board Risk Committee on 2026 (estimated Q1 2026 meeting), with flexibility for emerging risks and quarterly reporting required.

**Evaluation comment (grading notes, not shown to artmind):** Tests aggregation across audit planning and risk materiality. Use the risk-based audit priorities and 2026 plan in `governance/internal_audit_charter.md`, the risk appetite/KRIs in `risk_compliance/risk_appetite_statement_2026.md`, findings in `risk_compliance/audit_report_q4_2025.md`, and oversight in `governance/board_risk_committee_charter.md`. A good answer explains its prioritisation, not merely lists documents.

## Q23 — Build a compliant customer response after an incident

**Question:** Following a data breach, what must our customer communication include, when must it be sent, and which teams need to review it before release?

**Answer** (completed, 13 turns, 58.1s, $0.1199):

I'll search the artmind system for your data breach communication requirements and protocols.The question is about data breach customer communication requirements. This spans multiple domains: `banking.policy` (for breach notification policies), `banking.sop_guides` (for procedures), and `banking.risk_governance` (for incident response). Let me search these domains.Good. Now let me search for entities related to data breach notification, customer communication, and incident response.Excellent. I have several highly relevant entities. Let me pull detailed context on the key ones: the breach notification content requirement, the customer notification policy, and the notification timeline requirement.Good. Now let me get more detail on the customer notification policy and any team review requirements.Now let me check for any specific SOP or procedure details, and look for information about which teams need to review before release.Let me search for information about which specific teams/roles need to review the communication before release.Let me pull the actual SOP fraud investigation procedure to see if it details the review steps.Let me read the saved file to get the full details.Perfect. Now let me search for information about team review requirements specifically for customer communications in the policy documents.Based on the comprehensive information I've retrieved from the artmind system, I can now provide a complete answer.

## Data Breach Customer Communication Requirements

**Grounded in:** banking.policy (policy_privacy.md, policy_information_security.md), banking.sop_guides (fraud_investigation_procedure.md), banking.risk_governance domains.

### **What Must Be Included**

Customer breach notifications must include the following information (from **Breach Notification Content Control** and **Customer Notification for Personal Data Breach** requirements):

1. **What happened** — Description of the incident
2. **What data was affected** — Specific data types compromised (e.g., account numbers, personal information)
3. **Mitigation steps taken** — Actions already undertaken to stop the breach and protect customers
4. **Contact information for questions** — How customers can reach the bank for concerns

---

### **When It Must Be Sent**

The notification must follow a **dual-timeline approach**:

- **Customer Notification:** **Immediately** (without undue delay per GDPR)
- **Regulatory Notification:**
  - **FCA:** Within **72 hours** of confirmed data compromise
  - **ICO (UK):** Within **72 hours** for data breach report (if personal data breach confirmed)

The internal escalation must occur even faster:
- Breach identified → Escalate to **Data Protection Officer**
- Investigation within **24 hours**
- Assessment of risk level
- Notification decision

---

### **Teams That Must Review Before Release**

Based on the internal notification and approval structure:

1. **Data Protection Officer** — Primary escalation point, initiates notification review
2. **CTO, CRO, CEO** — Notified immediately for Severity 1–2 breaches
3. **Board** — Within 24 hours for Severity 1–2 incidents
4. **Head of Financial Crime** — If customer data compromise is confirmed
5. **Compliance Head** — For regulatory filing approval
6. **Head of Legal** — Coordinates regulatory notification and legal review

The notification communication itself must be reviewed by the **Data Protection Officer or designated notification team** (the appointed responsible party per the control measure) before release to customers.

**Sources grounded in:**
- **banking.policy**: policy_privacy.md (Data Breach Response section), policy_information_security.md (Notification & Reporting section)
- **banking.sop_guides**: fraud_investigation_procedure.md (Data Breach Notification Decision and reporting requirements)

**Evaluation comment (grading notes, not shown to artmind):** Tests incident-to-communications traversal. Use the customer-notification material and escalation/ownership in `reference/incident_response_plan.md`, privacy obligations in `policies/policy_privacy.md`, approved wording/resources in `templates/email_templates.md` and `templates/sms_templates.md`, and organisational roles in `organization/departments.md`.

## Q24 — Who owns a customer-impacting technology issue?

**Question:** A recurring online-banking error causes incorrect balances and complaints. Who owns the technical fix, who owns the customer response, and who should be kept informed if the issue is material?

**Answer** (completed, 15 turns, 69.6s, $0.1356):

Several domains are relevant. Let me narrow to the most pertinent for ownership of technical fixes and customer response:Let me search for escalation and technical fix ownership more directly:Good. Now let me get the actual escalation matrix details and look for technical fix ownership:Let me look at the full escalation matrix document and search for information about technical issues and system errors:Now let me search for complaint-handling roles and responsibilities, and information about who owns technical fixes:Good. Now let me look at the complaint policy in detail for responsibilities:Now let me search for who owns complaints handling and customer communication:Now let me find IT/CTO responsibility for technical fixes:Now let me search for information about CRO/CEO escalation and who gets informed:Perfect. Now let me get more specifics from the complaint policy about systemic issues:Let me also check the organizational structure for CRO and risk management:Excellent. Now I have sufficient information. Let me compile the answer based on the artmind data I've retrieved.

---

## Answer

Based on FirstUK Bank's governance documents, here are the ownership responsibilities for a recurring online-banking error causing incorrect balances and customer complaints:

### **Technical Fix Ownership**
**Chief Technology Officer (CTO)** and **IT Operations** own the technical fix. Per the escalation matrix (ESC-001, banking.sop_guides), when a system error causes balance issues:
- **IT Operations** handles initial resolution (Level 1-2, <4 hours)
- **CTO** assumes responsibility if the outage exceeds 1-4 hours with significant customer impact (Level 2-3)
- For critical/ongoing issues (>4 hours), the **CEO and CTO** jointly manage (Level 4-5)

The complaint policy (policy_complaints.md, banking.policy) explicitly states: "If system error: Urgent IT escalation to fix."

### **Customer Response Ownership**
**Complaints Manager** and **Head of Customer Service** own the customer response:
- **Frontline staff** initially acknowledge the complaint
- **Complaints Manager** (Level 2, supervisor authority) investigates and resolves moderate complaints within 5 business days
- **Head of Customer Service** (Level 3, director authority) handles significant complaints with financial compensation >£500
- For recurring/systemic issues affecting multiple customers, this escalates to **CEO/Board** (Level 4-5)

The policy requires complaints handlers to communicate clearly with customers regarding resolution status and provide appropriate compensation (full balance correction plus 8% interest if bank error).

### **Escalation/Inform If Material**
If the issue is material (systemic, affecting multiple customers, significant financial loss):

- **Head of Compliance/Compliance Head** — must be notified per complaint escalation procedures
- **Chief Risk Officer (CRO)** — escalation triggers at £10k-£100k loss (report within 24 hours); >£100k goes to Board
- **Chief Executive Officer (CEO)** — informed for complaints with >£500 compensation or >£100k financial impact
- **Board of Directors** — notified for:
  - Major financial loss (>£100k)
  - Reputational/regulatory concern
  - Systemic operational failure (>4 hour outage per ESC-001)

Per policy_operational_risk.md: losses >£100k require immediate Board + regulatory notification.

### **Grounded Sources**
- **escalation_matrix.md** (banking.sop_guides) — operational escalation procedures
- **policy_complaints.md** (banking.policy) — complaint handling authority and escalation
- **policy_operational_risk.md** (banking.policy) — incident escalation triggers and Board notification thresholds
- **organisation_model.md** (banking.organization) — executive decision authority structure

**Evaluation comment (grading notes, not shown to artmind):** Tests ownership resolution across `reference/technology_application_landscape.md`, `organization/organisation_model.md`, `organization/departments.md`, `reference/incident_response_plan.md`, `policies/policy_complaints_v3.md`, and `governance/board_risk_committee_charter.md`. The answer should distinguish operational accountability from technical ownership and Board-level oversight rather than naming one generic “manager.”

## Q30 — Open Banking delivery with a residual exception

**Question:** Did FirstUK meet the Open Banking deadline? What was delivered, what limited gap remains, who owns it, and how should its status be described?

**Answer** (completed, 7 turns, 29.6s, $0.0615):

I'll search the artmind knowledge graph for information about FirstUK and the Open Banking deadline. Let me start by identifying the relevant domains.I can see there's a document called "open_banking_delivery_outcome_2026_07.md" in the banking.reference domain. Let me search for FirstUK and Open Banking information across the relevant domains.Good! I found a relevant entity. Let me now retrieve the detailed information from the "open_banking_delivery_outcome_2026_07.md" document and query for more context about the Open Banking delivery status.Perfect! I have the key information. Let me get more details by checking if there are any related entities or findings about this delivery.Excellent! I now have comprehensive information to answer all parts of the question. Let me compile the answer.

---

## Answer

**Did FirstUK meet the Open Banking deadline?** Yes. FirstUK met the 2026-06-30 core delivery date for Open Banking compliance.

**What was delivered:**
- OAuth 2.0 APIs providing consented access to customer account, transaction, standing-order, and direct-debit mandate data
- Consent scope, expiry, and revocation controls 
- API-access data purging after 90 days unless renewed

**What limited gap remains:**
For 37 inactive legacy mandates, the API returns the mandate identifier and status but does not provide a historical cancellation timestamp. Support provides the timestamp within one business day as a workaround while migration to full compliance completes by 2026-08-15.

**Who owns it:**
The Chief Technology Officer (CTO) owns closure of this exception and is responsible for reporting it to the Board Risk Committee in September 2026.

**How should its status be described:**
"Core delivery complete; limited exception under remediation" (as stated in the delivery outcome document dated 2026-07-10). Compliance formally logged the exception on 2026-06-30.

**Grounding:** All details sourced from the **open_banking_delivery_outcome_2026_07.md** document in the **banking.reference** domain (Document ID: OB-OUTCOME-2026-07, reported 2026-07-10, regulatory reference FCA-OP-2026-01).

**Evaluation comment (grading notes, not shown to artmind):** Tests delivery status without overclaiming compliance. Use `reference/open_banking_delivery_outcome_2026_07.md` and `regulations/regulatory_circulars_2026.md`; identify the 37-mandate exception, workaround, 2026-08-15 remediation, CTO ownership, and Board route.

## Q31 — Coordinate the CASE-2026-041 response

**Question:** Give an integrated action plan for CASE-2026-041 covering containment, customer complaints, privacy/retention, AML, and governance reporting.

**Answer** (completed, 13 turns, 74.6s, $0.1122):

I'll retrieve the integrated action plan for CASE-2026-041 by querying the artmind system.Now I'll retrieve the CASE-2026-041 details from the cases domain:Now let me retrieve the detailed case information and the remediation actions:Now let me retrieve all the remediation actions and related case documents:Let me retrieve the complaints and incident timeline information:Now let me retrieve the incident timeline and retention decision:Now let me retrieve the retention decision:Now let me fetch related policies and governance information:Let me get governance and AML-related information:Now let me fetch AML-specific guidance:Perfect. Now I have comprehensive information across all five domains required. Let me compile the integrated action plan:

## Integrated Action Plan for CASE-2026-041: Customer Data Exposure

**Grounded in:** banking.cases (case documents), banking.policy (privacy/retention/AML), banking.sop_guides (complaints procedures), banking.risk_governance (governance reporting)

---

### **CASE OVERVIEW**
- **Incident:** Customer-document export configuration defect exposed statement PDFs outside intended account scope  
- **Detection:** 2026-07-02 09:18 UTC via monitoring alert  
- **Status:** Open — forensic reconciliation pending  
- **Impact:** Security assessment identified **120 potentially affected customers**; Operations confirmed **118 affected customers** after removing two failed-download events (reconciliation unresolved — both figures required in all reporting)

---

### **1. CONTAINMENT (CTO Owner)**

| Action | Status | Timeline |
|--------|--------|----------|
| **Security Patch Deployment** | Pending | Pre-closure gate |
| **Peer Review of Remediation** | Pending | Pre-closure gate |
| **Account-Scope Validation** | Pending | Pre-closure gate |
| **30-Day Monitoring Period** | Pending | Post-patch, 30 days |
| **Root-Cause Ownership Assignment** | Pending | Pre-closure gate |

**Remediation Sequence:**
1. Deploy security patch addressing anomalous export access vulnerability
2. Peer review of patch and remediation measures before implementation
3. Validate account scope against export logs (reconcile 120 vs. 118 count)
4. Monitor for 30 days for recurrence of anomalous export access
5. Assign root-cause investigation team and establish accountability

---

### **2. CUSTOMER COMPLAINTS (Complaints Manager Owner)**

**Representative Complaint Received:** 2026-07-06  
**Customer Requests:** Account explanation, compensation for distress, subject access, erasure

**Complaint Handling Process:**
- **Acknowledgment:** Verify identity and accessibility needs immediately
- **Investigation:** Investigate individual exposure (required for each complaint)
- **Explanation:** Do NOT finalize impact count; attribute 120 (Security potential) and 118 (Operations confirmed) separately. Privacy and Incident Commander must review all communications before sending.
- **Escalation Rights:** Explain customer's escalation rights per complaint resolution guide
- **Privacy Review:** Escalate through Customer Complaints Resolution Process (three severity tiers: Low, High, Critical) with defined response timelines
- **Governance:** Report complaint status and trajectory to CRO for governance reporting (see section 5)

**Retention During Complaint:** Incident, complaint, and notification records must be retained and restricted pending complaint closure (not a gate for other remediation, but a separate hold).

---

### **3. PRIVACY & DATA RETENTION (DPO Owner)**

**Retention Decision Status:** Active legal and regulatory hold (as of 2026-07-07)

**Data-Specific Handling:**

| Data Category | Action | Authority | Review Date |
|---------------|--------|-----------|-------------|
| **Marketing Preferences** | Suppress immediately if requested | Customer-driven | Upon request |
| **Unneeded Duplicate Copies** | Restrict and review for deletion | DPO discretion | Before erasure review |
| **Incident, Complaint & Notification Records** | Retain and restrict | Legal hold | 2026-10-07 or case closure |
| **AML/KYC & Transaction Evidence** | Retain under regulatory obligation | Regulatory mandate | Ongoing, post-closure review |
| **Subject Access Material** | Provide electronically after identity verification | GDPR Subject Access | Upon verified request |

**Erasure Decision:**
- **Current:** NOT approved while incident, complaint, regulatory assessment, and AML hold remain open  
- **Hold Review Point:** 2026-10-07 (3 months post-incident) AND after all matters close  
- **Customer Communication:** Explain hold and review point WITHOUT stating erasure is permanently denied

**Legal Basis for Hold:**
- Regulatory exception: AML/KYC records must be retained under Anti-Money Laundering Compliance Obligation (5–6 years post-closure per regulatory framework)
- Privacy justification: Legal claim defense, regulatory investigation, fraud prevention during incident investigation

---

### **4. AML & FINANCIAL CRIME (Financial Crime Owner)**

**AML Records Retention Obligation:**
- **Regulatory Mandate:** Anti-Money Laundering Regulations require retention of KYC and AML records for **5–6 years post-closure**
- **Scope:** All KYC/AML/transaction evidence linked to the 118 (or 120 under Security assessment) affected customers  
- **Holds Status:** Already in place as mandatory remediation action; NOT subject to customer erasure request

**Reporting Requirements:**
- **Suspicious Activity:** Assess whether export access constitutes reportable suspicious activity; coordinate with Compliance
- **Customer Communication:** Do NOT disclose AML-hold reasons in plain language; reference "regulatory obligation" only
- **Post-Closure Review:** AML records subject to retention-decision review in October 2026, but post-closure hold likely to extend beyond closure date

---

### **5. GOVERNANCE REPORTING (CRO Owner)**

**Incident Reporting Protocol:**

**To Board Risk Committee / Governance Body:**
- **Incident Summary:** Customer-document export defect, contained 2026-07-02  
- **Impact Figures:** Attribute both: "Security identified 120 potentially affected customers; Operations confirmed 118 affected customers; reconciliation pending final approval"  
- **Remediation Status:** Patch pending peer review, 30-day monitoring, root-cause assignment; all pre-closure gates active  
- **Regulatory & Compliance Status:** AML hold in place; Privacy DPO hold through 2026-10-07; complaint under investigation  

**Risk Appetite Alignment:**
- **Operational Loss Risk:** Customer data exposure event; assess against 2026 Risk Appetite Statement thresholds (capital, operational loss, compliance violation limits)  
- **Compliance Violation Risk:** Monitor pending regulatory notification (if triggered); assess against compliance violation threshold  

**Audit Implications:**
- **2026 Audit Plan:** Account Opening & KYC is priority audit area; this incident may inform Q2–Q3 audit scope  
- **Controls Finding:** Export access controls failure; document as control deficiency for Internal Audit quarterly reporting

**Disclosure:**
- **Regulatory Notification:** Coordinate with DPO/Compliance on threshold for regulator notification (timing depends on final impact confirmation and customer harm assessment)  
- **Public Disclosure:** Not confirmed; assess reputational risk and regulatory requirement for public statement vs. incident-specific notification to affected customers only

**Closure Criteria (for governance sign-off):**
1. Security patch deployed AND peer-reviewed ✓  
2. Account scope reconciliation approved (120 vs. 118 resolution) ✓  
3. 30-day monitoring complete with no recurrence ✓  
4. Root-cause report delivered with ownership assigned ✓  
5. Customer complaint resolution and compensation (if applicable) ✓  
6. Privacy hold review completed (2026-10-07) ✓  
7. All regulatory/AML holds confirmed sustainable ✓  

---

### **WORKSTREAM OWNERS & ESCALATION**

| Function | Owner | Escalation | Closure Gate? |
|----------|-------|-----------|---------------|
| **Containment** | CTO | Incident Commander | YES (technical remediation) |
| **Complaints** | Complaints Manager | Chief Operating Officer | NO (parallel track) |
| **Privacy & Retention** | DPO | Privacy Officer / Legal | NO (legal hold, not closure gate) |
| **AML Records** | Financial Crime | Chief Compliance Officer | NO (regulatory hold, ongoing) |
| **Governance Reporting** | CRO | Board Risk Committee | YES (sign-off on closure) |

---

**Sources:**
- [case_2026_041_overview.md](case_2026_041_overview.md) — incident summary, impact counts (banking.cases)  
- [case_2026_041_incident_timeline.md](case_2026_041_incident_timeline.md) — chronology and remediation sequence (banking.cases)  
- [case_2026_041_complaint_record.md](case_2026_041_complaint_record.md) — customer complaint handling (banking.cases)  
- [case_2026_041_retention_decision.md](case_2026_041_retention_decision.md) — data retention and erasure framework (banking.cases)  
- [policy_privacy.md](policy_privacy.md), [policy_retention.md](policy_retention.md), [policy_aml.md](policy_aml.md) — regulatory authority and hold justification (banking.policy)  
- [complaint_resolution_guide.md](complaint_resolution_guide.md), [escalation_matrix.md](escalation_matrix.md) — complaints procedures (banking.sop_guides)  
- [governance_framework_overview.md](governance_framework_overview.md), [board_risk_committee_charter.md](board_risk_committee_charter.md) — governance reporting structure (banking.risk_governance)

**Evaluation comment (grading notes, not shown to artmind):** Tests end-to-end traversal through the four `cases/case_2026_041_*.md` records and their linked incident, privacy, retention, AML, complaint, and governance documents. Preserve workstream ownership and distinguish immediate response from later review.

## Q32 — Vulnerable customer with an unresolved complaint

**Question:** Which vulnerable customer currently has an open, unresolved complaint, and what extra care does our training and complaints guidance require when handling their case?

**Answer** (completed, 20 turns, 100.7s, $0.1726):

This is a Records-plus-guidance question. The vulnerable customers table should have the current complaint records, and I'll need to cross-reference training and complaints guidance documents. Let me first check the database bridge and then query the structured data.Now I'll query the vulnerable_customers table and join it to complaints to find open, unresolved cases:Now I'll query for vulnerable customers with open complaints:Excellent. Now I need to find the training and complaints guidance that applies to this vulnerability driver and support need. Let me search the training and complaints policy documents:Good—I found relevant guidance on vulnerable customers. Let me get more specific guidance on safe space and life events, and the complaints handling procedures:Now let me pull the actual text of the complaint resolution guide and the vulnerable customer handling guidance:Let me correct the syntax:Let me search more specifically for complaint handling procedures with vulnerable customer care:Let me retrieve the full complaint policy document to find specific vulnerable customer guidance:The v2 policy was superseded in June 2026. Let me check the current v3 policy:Let me retrieve the full v3 policy document:Let me search more directly for guidance on safe space and vulnerable customers:Let me run the actual Cypher:Let me search with vector-text for more specific guidance on complaint handling for this customer's vulnerability:Perfect! I've found the key guidance. Based on my retrieval from the artmind system, here is the answer:

## Answer

**Vulnerable Customer with Open Complaint:**  
**CUST-0019** currently has an open, unresolved complaint (**CMP-0015** — a Service complaint). This customer is classified as vulnerable due to **Life Events** and requires **Safe Space** support.

---

## Extra Care Required — Training & Complaints Guidance

According to **branch_operations_training.md** (banking.communications domain), the training guidance for vulnerable customers experiencing life events who need safe space specifies:

> **Vulnerable Customers**
> - Abuse victims: **Offer safe space, confidential help**
> - Elderly or mentally incapacitated: **Slow down, be patient**

For this customer specifically, the key requirements are:

1. **Safe Space** — Conduct complaint handling in a private, confidential setting away from public areas or other staff overhearing conversations
2. **Confidentiality** — Maintain strict confidentiality about the customer's situation and vulnerability status
3. **Patience & Empathy** — Slow down communications, allow extra time, and acknowledge the emotional context of life events
4. **Data Security** — Per branch operations training: Never discuss this customer's information where others can hear; file documents securely; take extra care with privacy given their vulnerability

Additionally, the **policy_complaints.md** (banking.policy domain) training requirements state that:
- **Customer-facing staff** handling this case require **2-hour practical scenarios + role play** as part of mandatory complaint handling training
- **All staff** need **1-hour complaint handling training** annually

The complaint is categorized as **Service** severity, so it should follow the standard 8-calendar-day resolution timeframe under FCA requirements, but given the customer's vulnerability status, particular care should be taken to provide empathetic communication and avoid adding distress during the investigation and resolution process.

**Sources:**  
- branch_operations_training.md (banking.communications domain)
- policy_complaints.md (banking.policy domain)  
- vulnerable_customers table (banking domain, structured store)  
- complaints table (banking domain, structured store)

**Evaluation comment (grading notes, not shown to artmind):** Tests resolving a structured record to the right graph guidance. Joining `structured/complaints.csv` (`status = 'Open'`) against `structured/vulnerable_customers.csv` on `customer_id` surfaces exactly one match: CUST-0019, driver `Life Events`, support need `Safe Space`, complaint category `Service`. The answer should combine `training/branch_operations_training.md`'s vulnerable-customer guidance (abuse victims: offer a safe space, confidential help) with `policies/policy_complaints_v3.md`'s timeframe/escalation rule for a Service complaint, not generic vulnerable-customer platitudes.

## Q33 — Escalation authority for the largest Account Takeover payout

**Question:** What is the largest compensation amount recorded for an Account Takeover complaint, and per the complaint escalation matrix, what authority level and timeline would that payout require?

**Answer** (completed, 8 turns, 32.3s, $0.0749):

I'll help you find the largest Account Takeover compensation and the associated escalation requirements. Let me start by identifying the relevant domain.Now I'll search for Account Takeover complaints and compensation information. The structured data in `banking` domain appears to have complaint records. Let me check the database bridge first:Good, the `complaints` table has compensation data. Let me also check the escalation matrix document. Let me query for Account Takeover complaints with compensation amounts:Now let me query for the largest Account Takeover compensation amount:Perfect! The largest Account Takeover compensation is £600. Now I need to find the escalation matrix details to determine the authority level and timeline for a £600 payout:Perfect! I now have all the information needed to answer the question. Based on the data retrieved:

## Answer

**Largest Account Takeover Compensation:** £600

**Escalation Matrix Requirements for £600 Payout:**

According to the escalation_matrix.md (banking.sop_guides domain), a £600 compensation payout for an Account Takeover complaint falls under **Level 2: Supervisor/Manager Resolution**.

- **Authority Level:** Supervisor, Branch Manager, or Complaints Handler
- **Approval Limit:** £500–£5,000 (£600 fits within this range)
- **Timeline:** 1–3 business days
- **Response Time Commitment:** 2 hours for acknowledgment, 2 days for investigation, 3 days for decision

The escalation matrix also specifies that for **Customer Complaints with High Severity (Significant financial loss)**, the initial handler would be a Complaints Manager at **Level 3–4** with a timeline of **24–48 hours**, which may apply if the Account Takeover is classified as high severity.

**Sources:** 
- complaints table (banking domain) — largest compensation amount data
- escalation_matrix.md (banking.sop_guides domain) — escalation levels, authority limits, and timelines

**Evaluation comment (grading notes, not shown to artmind):** Tests a resolve-then-govern chain, mirroring the reasoning in Q07–Q09 but sourced from real transaction data instead of a hypothetical figure. `structured/complaints.csv` filtered to `category = 'Account Takeover'` gives two rows — CMP-0018 (£600) and CMP-0008 (£500) — so the largest is unambiguous. The answer should then apply `policies/policy_complaints_v3.md`'s (or `policy_complaints.md`'s, if the decision date requires the earlier version) escalation-by-loss table: payouts above £500 require Director / Head of Customer Service approval within 8 days, not frontline sign-off.

## Q34 — Fee-dispute customer satisfaction versus the policy remedy

**Question:** For customers who raised a Fee Dispute complaint on their SmartSaver Account, what is their average CSAT score, and what does the complaints policy require when a fee was applied correctly versus in error?

**Answer** (completed, 10 turns, 45.6s, $0.0820):

I'll query the artmind system to find information about Fee Dispute complaints on SmartSaver Accounts, including CSAT scores and complaints policy requirements.I'll help you find information about CSAT scores for Fee Dispute complaints on SmartSaver Accounts and the complaints policy requirements. Let me start by exploring the available domains.Good. I can see there's a `banking` domain with structured tables (including `csat_scores` and `complaints`) and a `banking.policy` domain for policies. Let me check the bridge to understand the table structure and then query for Fee Dispute complaints on SmartSaver Accounts.Now let me check the schema for the complaints and csat_scores tables to understand their structure better:Now let me check the csat_scores schema:Perfect. Now I need to query for Fee Dispute complaints on SmartSaver Accounts and their CSAT scores. Let me run a SQL query:Excellent! The average CSAT score is 4.33. Now I need to find information about what the complaints policy requires when a fee was applied correctly versus in error. Let me search the policy domain:Now let me search for information about fees and complaints policy requirements:Perfect! I have found the relevant information. Let me compile the answer:

## Answer

Based on the artmind data:

**Average CSAT Score for Fee Dispute Complaints on SmartSaver Accounts:** 
The average CSAT score is **4.33/5** for customers who raised a Fee Dispute complaint on their SmartSaver Account.
*Source: banking domain, complaints and csat_scores tables*

**Complaints Policy Requirements (Fee Applied Correctly vs. In Error):**
According to the complaints policy (policy_complaints.md and policy_complaints_v3.md in the banking.policy domain), the requirements for handling Fee Disputes are:

- **If fee applied correctly per Terms & Conditions:** Explain and decline reversal
- **If fee error:** Reverse charge + interest
- **If unclear T&Cs:** Refund + apology

The investigation process requires reviewing the terms & conditions, checking if the fee was applied correctly, assessing if the fee was disclosed clearly, and reviewing customer history before determining the appropriate resolution.

*Sources: policy_complaints.md and policy_complaints_v3.md (banking.policy domain)*

**Evaluation comment (grading notes, not shown to artmind):** Tests keeping a SQL aggregate and a two-branch policy answer distinct rather than merging them. Joining `structured/complaints.csv` (`category = 'Fee Dispute'`, `product = 'SmartSaver Account'`) to `structured/customers.csv` and `structured/csat_scores.csv` covers four customers (CUST-0001, CUST-0009, CUST-0014, CUST-0021) with six survey rows. The policy answer should separately state: fee applied correctly per T&Cs → explain and decline reversal; fee error → reverse charge + interest (`policies/policy_complaints.md` / `policy_complaints_v3.md`, Fee Disputes).

## Q35 — Branch manager vulnerable-customer review load

**Question:** Which branch manager has reviewed the most vulnerable-customer cases on record, and according to branch operations training, what should they be doing differently depending on each case's vulnerability driver?

**Answer** (completed, 17 turns, 77.4s, $0.1477):

I'll help you find which branch manager has reviewed the most vulnerable-customer cases and what the training guidance says. Let me start by exploring the available domains.Now let me check the structured data to find branch managers and vulnerable-customer cases:Good, I can see there's a `vulnerable_customers` table. Let me check the schema and then query the data to find which branch manager has reviewed the most cases:Now let me query the vulnerable_customers table to find the branch manager who reviewed the most cases:I see all managers have reviewed exactly one case each. Let me check the actual vulnerable_customers data to see all cases and their details:Since each manager has reviewed exactly one case, I cannot identify a single manager who reviewed "the most" cases. However, I notice the question asks what training says they should be doing differently depending on the vulnerability driver. Let me search the branch operations training for guidance on handling vulnerable customers by vulnerability driver:Good, I found guidance in the branch operations training. Let me get the full section on vulnerable customers with more detail:The training section I found maps vulnerability types (elderly/mentally incapacitated, language barriers, abuse victims, financial abuse) but not explicitly to the vulnerability drivers in the data (Capability, Health, Life Events, Resilience). Let me search more specifically for how to handle each vulnerability driver type:The training document shows generic vulnerable customer guidance but doesn't break down by the specific vulnerability drivers. Let me search across all communication domains for more specific differentiated guidance:The search is not finding specific guidance mapped to the four vulnerability drivers. Let me check if there's an escalation matrix or complaint resolution guide that might provide differentiated guidance:The escalation matrix doesn't have specific guidance on vulnerability drivers. Let me search the complaint resolution guide for guidance on handling vulnerable customer cases by type:Let me get the full complaint resolution guide to see if it contains specific guidance on handling vulnerable customers by vulnerability driver:Let me search the full complaint resolution guide document to find guidance specific to vulnerable customers:The documents don't seem to have differentiated guidance for each vulnerability driver. Let me try searching the risk governance domain which might have framework guidance:Based on my research through the artmind knowledge graph and structured data, I can now provide an answer:

## Answer

**Finding on Case Review Counts:**
According to the artmind data, there is **no single branch manager who has reviewed significantly more vulnerable-customer cases than others**. The structured data shows that each of the seven identified managers (Michael O'Brien, Emma Thompson, Fiona MacLeod, Thomas O'Donnell, James Chen, Andrew Stewart, and Peter Newton) has reviewed exactly **one vulnerable-customer case each** on record. Therefore, the premise of identifying a manager with "the most" cases cannot be satisfied from the available data.

**Branch Operations Training Guidance:**
According to the **branch_operations_training.md** document (banking.communications domain, effective 2026-01-15), the guidance provided for vulnerable customers maps to specific vulnerability **characteristics** rather than the four specific vulnerability **drivers** in the data (Capability, Health, Life Events, Resilience):

The training states managers should differentiate their approach as follows:
- **Elderly or mentally incapacitated customers**: Slow down, be patient
- **Language barriers customers**: Arrange interpreter  
- **Abuse victims**: Offer safe space, confidential help
- **Financial abuse cases**: Escalate to compliance

However, **the artmind corpus does not contain specific differentiated guidance mapped explicitly to the four vulnerability drivers** (Capability, Health, Life Events, Resilience) found in the vulnerable_customers table. The training material addresses vulnerable customer handling generically rather than by driver-specific approach.

**Grounded in:** banking domain structured data (vulnerable_customers table), branch_operations_training.md (banking.communications domain), and escalation_matrix.md (banking.sop_guides domain)

**Evaluation comment (grading notes, not shown to artmind):** Tests refusing false precision. `structured/vulnerable_customers.csv` grouped by `reviewed_by` shows seven different branch managers, each with exactly one case — there is no single "busiest" reviewer, and a good answer says so instead of naming one arbitrarily. It should then map each represented driver to `training/branch_operations_training.md`'s per-driver guidance: Health/Capability (elderly or mentally incapacitated) → slow down, be patient; Capability (language barriers) → arrange an interpreter; Life Events (abuse victims) → offer a safe space; Resilience (financial abuse) → escalate to compliance.

## Q36 — CSAT gap between open and resolved complaints

**Question:** Do customers with an open, unresolved complaint show a different average CSAT score from customers whose complaints have already been resolved, and what does the complaints policy say should happen if a response is at risk of running late?

**Answer** (completed, 23 turns, 104.9s, $0.2288):

I'll help you find answers to both of these questions. Let me start by exploring the available domains to determine where this information would be stored.Good. I can see that the `banking` domain has structured data with tables including `complaints` and `csat_scores`, and the `banking.policy` and `banking.sop_guides` domains have policy documents. Let me check the structured data first for the CSAT comparison, and then look for policy guidance on late responses.Perfect. I can see there are `complaints` and `csat_scores` tables. Let me query the schema for these tables to understand the structure, then run SQL to answer the CSAT question.Good. Now let me check the `csat_scores` schema as well:Perfect. Now I'll run SQL to compare the average CSAT score for customers with open complaints versus resolved complaints:The query returned no rows. Let me check the actual values in the complaints status column:The values have capital letters. Let me run the correct query:Excellent! Now let me search for the complaints policy regarding late response procedures:Good start, but I need more specific information about what happens when responses are at risk of running late. Let me search more specifically:Let me search more specifically for complaint deadline/at-risk procedures:I found the full document but the old version is superseded. Let me check the current version:Let me read the file directly to find the specific section on late response procedures:Let me search for sections that specifically address "at risk" or "breach" scenarios:Let me search the SOP guides for procedures about deadline management:Good, but I still need to find the specific procedure for when a response is at risk of running late. Let me search more specifically in the v3 policy:Let me search for more specific sections on deadline management and escalation procedures when at risk of missing deadlines:Let me search for more sections that may cover deadline management and late response procedures:Let me check the remaining chunks of the document:Let me check chunk 36-38 for any additional guidance on deadline management:Based on my search, the policy_complaints_v3.md document doesn't have an explicit section on what should happen when a response is at risk of running late. Let me search more broadly across the SOP guides:Based on my thorough search of the artmind knowledge base, I can now provide you with answers to both questions:

## Answer

**Q1: Average CSAT Score Comparison (Open vs. Resolved Complaints)**

Yes, there is a measurable difference in average CSAT scores between the two groups (based on the **banking** domain structured data):

- **Customers with Open/Unresolved Complaints:** Average CSAT score of **3.0** (based on 1 customer)
- **Customers with Resolved Complaints:** Average CSAT score of **4.25** (based on 6 customers)

This represents a **1.25-point difference** on the CSAT scale, indicating that customers whose complaints have been resolved report notably higher satisfaction than those with open complaints.

**Grounded in:** Structured data from `complaints` and `csat_scores` tables in the **banking** domain (dated 2026-08-28).

---

**Q2: What the Complaints Policy Says About Late Responses**

The current **policy_complaints_v3.md** (effective 2026-06-01, in the **banking.policy** domain) establishes specific response timeframes:

- **Standard complaints:** 8 calendar days maximum
- **Simple/quick-resolve complaints:** 1 business day  
- **Complex complaints:** 8 weeks (with possible 8-week extension if investigation justified and customer agrees)

However, **the policy does not contain an explicit procedure for what should happen if a response is at risk of running late.** The document specifies timeframe requirements and escalation triggers (e.g., "Director if unresolved" for standard complaints), but does not address proactive notification, extension requests, or remediation steps when a deadline is at risk of being missed.

**Grounded in:** **policy_complaints_v3.md** (Chunk 15/38 on Response Timeframes and Chunks 21/38 on Escalation Matrix), **banking.policy** domain. The complaint resolution customer-facing guide (**complaint_resolution_guide.md**, **banking.sop_guides** domain) similarly does not detail this procedure.

---

**Ambiguity noted:** The question's second part assumes procedural guidance exists in the complaints policy regarding "at-risk" responses. The available documents define deadlines and escalation authority but do not explicitly prescribe actions when those deadlines are at risk of breach.

**Evaluation comment (grading notes, not shown to artmind):** Tests not overclaiming from a thin sample — an intentional parallel to Q28's "distinguish, don't collapse" pattern. Only two customers have an `Open` complaint in `structured/complaints.csv` (CUST-0019, CUST-0024), and only CUST-0019 has any `structured/csat_scores.csv` rows (a single survey, score 3) versus 25 CSAT rows across resolved-complaint customers (avg. ~3.6) — a good answer flags the n=1 comparison as too thin to generalise rather than asserting a trend, and notes CUST-0024 has no CSAT record at all. It should then use `policies/policy_complaints.md` / `policy_complaints_v3.md`'s 8-day/8-week response clock and escalation rule for at-risk responses.
