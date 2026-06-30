# FirstUK Bank — Business Ontology

## Metadata

| Field | Value |
|-------|-------|
| Document ID | ONTO-2026-001 |
| Version | 1.4 |
| Effective Date | 2026-01-15 |
| Review Date | 2026-12-31 |
| Owner | Enterprise Architect |
| Department | Technology |
| Status | Active |
| Classification | Internal |
| Audience | Architects, Data Engineers, Domain Experts |
| Related Documents | [[products]], [[systems]], [[departments]] |
| Standards Reference | FIBO (Financial Industry Business Ontology), ISO 20022 |

---

## Executive Summary

The Business Ontology defines the core concepts, entities, relationships, and constraints that govern FirstUK Bank's enterprise. It provides a shared semantic understanding of business domains, enabling effective knowledge representation, data integration, and enterprise reasoning.

**Entities Defined:** 30+  
**Relationships:** 50+  
**Constraints:** 100+  

---

## Ontology Principles

1. **Realism:** Concepts map to real-world business entities
2. **Consistency:** Concepts maintain consistent definitions across documents
3. **Completeness:** Critical business concepts are defined
4. **Clarity:** Definitions are unambiguous and precise
5. **Traceability:** Concepts link to policies, procedures, and systems

---

## Core Domains

1. **Party & Customer Domain** — People and organizations interacting with the bank
2. **Product Domain** — Banking products and services
3. **Account Domain** — Customer accounts and holdings
4. **Transaction Domain** — Money movement and account activity
5. **Risk & Compliance Domain** — Regulatory and risk concepts
6. **Organization Domain** — Organizational structure and roles

---

## 1. Party & Customer Domain

### Entity: Party

**Definition:** Any natural person or organization that interacts with FirstUK Bank.

**Subtypes:**
- Natural Person (individual customer)
- Organization (business customer, future)
- Employee
- Regulatory Authority

**Key Attributes:**
- `party_id` — Unique identifier
- `party_type` — Type of party (individual, organization)
- `name` — Full legal name
- `registration_date` — Date party entered relationship with bank

---

### Entity: Customer

**Definition:** A natural person (individual) who holds at least one account with FirstUK Bank.

**Attributes:**
- `customer_id` — Unique identifier (linked to Party)
- `first_name` — Given name
- `last_name` — Family name
- `date_of_birth` — DoB (required for AML)
- `nationality` — Country of citizenship
- `email` — Email address
- `phone` — Primary phone number
- `address` — Registered address (see Address concept)
- `customer_status` — Active, Dormant, Closed, Suspended

**Key Business Rules:**
- Every customer must have a valid identity (see KYC concept)
- Every customer must pass AML screening before account opening
- Customer can hold multiple accounts
- Customer can have joint account holders

**Relationships:**
- `has_accounts` → Account (1:many)
- `has_address` → Address (1:many)
- `has_kyc_verification` → KYC_Verification (1:many)

**Example:**
```
Customer: Sarah Johnson
- customer_id: CUST-00001
- date_of_birth: 1985-03-15
- nationality: UK
- email: sarah.johnson@email.com
- customer_status: Active
- has_accounts: [ACC-00001, ACC-00002]
```

---

### Entity: Address

**Definition:** Physical location associated with a customer, account, or branch.

**Attributes:**
- `address_id` — Unique identifier
- `address_line1` — Street address (primary)
- `address_line2` — Street address (secondary, optional)
- `city` — City/town name
- `postcode` — UK postcode (format: A(1,2)9(1,2)A 9A(2))
- `country` — Country code (ISO 3166-1 alpha-2)
- `address_type` — Registered, Correspondence, Branch
- `effective_date` — When address became active

**Key Business Rules:**
- UK customers must have a UK address
- Address must be verified (utility bill, council tax, etc.)
- Addresses use UK postcode format

---

### Entity: Identification Document

**Definition:** Official document used to verify customer identity (KYC).

**Attributes:**
- `doc_id` — Unique identifier
- `customer_id` — Linked customer
- `doc_type` — Passport, Driving License, ID Card
- `doc_number` — Document number
- `issuing_country` — Country of issue
- `issue_date` — Date issued
- `expiry_date` — Expiration date (if applicable)
- `verification_status` — Unverified, Verified, Expired, Rejected

**Accepted Types (UK):**
- UK Passport
- UK Driving License
- UK National ID Card (future)

---

## 2. Product Domain

### Entity: Product

**Definition:** A financial service offered by FirstUK Bank (see [[products]]).

**Attributes:**
- `product_id` — Unique identifier (e.g., "SAV-001")
- `product_name` — Marketing name (e.g., "SmartSaver Account")
- `product_type` — Savings, Current, Mortgage, Card
- `description` — Product description
- `launch_date` — Product launch date
- `status` — Active, Inactive, Deprecated
- `min_balance` — Minimum opening balance (if applicable)
- `max_balance` — Maximum balance limit (if applicable)
- `target_customer` — Target market segment

**Relationships:**
- `has_features` → Feature (1:many)
- `has_pricing_tier` → PricingTier (1:many)

**Example:**
```
Product: SmartSaver Account
- product_id: SAV-001
- product_type: Savings
- min_balance: £1
- target_customer: General consumers, ages 18+
```

---

### Entity: Feature

**Definition:** A specific capability or service included in a product.

**Attributes:**
- `feature_id` — Unique identifier
- `feature_name` — Feature name (e.g., "Joint Account Support")
- `feature_description` — Detailed description
- `available_products` — Products offering this feature
- `effective_date` — When feature became available

**Common Features:**
- Easy Access (withdrawals without notice)
- Interest Accrual (variable or fixed)
- Online Banking
- Mobile App
- Debit Card
- Standing Orders
- Direct Debits
- Joint Account Support

---

### Entity: Interest Rate

**Definition:** A specific rate of interest applied to accounts.

**Attributes:**
- `rate_id` — Unique identifier
- `product_id` — Linked product
- `rate_type` — Fixed, Variable, Tiered
- `rate_value` — Annual percentage (e.g., 4.5)
- `rate_tier` — Balance threshold (e.g., £0–£10,000)
- `effective_date` — When rate became effective
- `next_review_date` — Planned rate review date
- `review_basis` — Linked to Bank of England base rate, margin

**Example:**
```
Interest Rate:
- product_id: SAV-001
- rate_type: Tiered Variable
- Tier 1: £0–£10,000 @ 4.5% AER
- Tier 2: £10,001–£50,000 @ 4.7% AER
- Tier 3: £50,001+ @ 4.8% AER
- review_basis: BoE base rate + 2.5% margin
- effective_date: 2026-01-15
```

---

## 3. Account Domain

### Entity: Account

**Definition:** A contractual arrangement between a customer and FirstUK Bank to hold money, make transactions, and earn interest.

**Attributes:**
- `account_id` — Unique identifier (e.g., "ACC-00001")
- `customer_id` — Primary account holder (links to Customer)
- `product_id` — Linked product (e.g., "SAV-001", see [[products]])
- `currency` — Account currency (GBP)
- `opening_date` — Date account opened
- `account_status` — Active, Dormant, Closed, Suspended
- `account_balance` — Current account balance (calculated from transactions)
- `available_balance` — Balance available for withdrawal
- `overdraft_limit` — Overdraft facility amount (if applicable)

**Key Business Rules:**
- Every account must have one primary account holder (customer)
- Account balance is calculated from transaction ledger
- Account can only be in one status at a time
- Closed accounts are not reactivated

**Relationships:**
- `has_primary_holder` → Customer (1:1)
- `has_account_holders` → AccountHolder (1:many, including joint holders)
- `has_products` → Product (1:1)
- `has_transactions` → Transaction (1:many)
- `has_standing_orders` → StandingOrder (1:many)
- `has_cards` → Card (1:many)

**Example:**
```
Account: ACC-00001
- customer_id: CUST-00001 (Sarah Johnson)
- product_id: SAV-001 (SmartSaver Account)
- opening_date: 2024-06-15
- account_status: Active
- account_balance: £5,250.00
- available_balance: £5,250.00
```

---

### Entity: Account Holder

**Definition:** A person with legal rights and responsibilities for an account.

**Attributes:**
- `holder_id` — Unique identifier
- `account_id` — Linked account
- `customer_id` — Linked customer
- `holder_type` — Primary, Joint, Secondary
- `signatory_required` — Whether signature required for withdrawals
- `added_date` — Date holder added to account

**Key Business Rules:**
- Account must have at least one primary holder
- Joint account: Both holders must pass KYC/AML
- Either joint holder can withdraw full balance
- Interest calculated on total balance (not per holder)

**Example:**
```
Joint Account ACC-00002:
- Primary Holder: John Smith (customer_id: CUST-00002)
- Joint Holder: Jane Smith (customer_id: CUST-00003)
- Either can withdraw, both liable for overdraft
```

---

### Entity: Joint Account

**Definition:** An account held by two or more customers with joint and several liability.

**Attributes:**
- `account_id` — Base account ID
- `joint_type` — Joint and Several, Tenants in Common
- `holders` → AccountHolder (2+ records)
- `interest_split` — How interest allocated (equal, specified %)

**Key Business Rules:**
- Both holders must consent to account closure
- Both holders responsible for overdraft
- Both holders can initiate transactions
- Dispute resolution per [[departments]] Complaint Handling

**Regulatory Treatment:**
- Both holders jointly and severally liable
- Both included in AML due diligence
- Both entitled to account information

---

### Entity: Card

**Definition:** A payment card (debit, credit, or prepaid) linked to an account.

**Attributes:**
- `card_id` — Unique identifier
- `account_id` — Linked account
- `customer_id` — Cardholder
- `card_type` — Debit, Credit, Prepaid
- `card_number` — 16-digit card number (masked in logs)
- `cardholder_name` — Name on card
- `expiry_date` — Card expiration (MMYY format)
- `cvv` — Security code (not stored after verification)
- `card_status` — Active, Inactive, Lost, Stolen, Expired, Blocked
- `issued_date` — Date card issued
- `daily_limit` — Daily transaction limit (e.g., £500 for ATM)

**Key Business Rules:**
- Card can be locked/unlocked by cardholder
- Lost/stolen cards can be replaced (fee £15)
- Only one card per account (initially)
- Contactless limit: £100 per transaction

**Relationships:**
- `linked_to_account` → Account (1:many)
- `has_transactions` → Transaction (1:many)

---

## 4. Transaction Domain

### Entity: Transaction

**Definition:** A financial event representing money movement or activity on an account.

**Attributes:**
- `transaction_id` — Unique identifier
- `account_id` — Linked account
- `transaction_type` — Deposit, Withdrawal, Transfer, Interest, Fee, Correction
- `amount` — Transaction amount (in pence for precision)
- `currency` — Currency (GBP)
- `transaction_date` — Date transaction occurred
- `settlement_date` — Date settlement completed
- `description` — Transaction description
- `status` — Pending, Completed, Failed, Reversed
- `reference` — Customer-visible reference

**Transaction Types:**

| Type | Direction | Trigger | Example |
|------|-----------|---------|---------|
| Deposit | Credit | Customer adds funds | Branch deposit |
| Withdrawal | Debit | Customer removes funds | ATM withdrawal |
| Transfer | Debit (sender) | Money moved to another account | FPS transfer out |
| Inbound Transfer | Credit | Money received | FPS transfer in |
| Interest | Credit | Interest accrual | Monthly interest |
| Fee | Debit | Service charge | Card replacement fee |
| Standing Order | Debit | Recurring transfer | Mortgage payment |
| Direct Debit | Debit | Bill payment | Utility payment |

**Key Business Rules:**
- Transaction cannot be deleted (audit trail)
- Account balance = sum of all transactions
- Each transaction linked to fraud risk score
- All transactions logged for compliance

**Relationships:**
- `on_account` → Account (1:many)
- `from_account` → Account (transfer source, optional)
- `to_account` → Account (transfer destination, optional)
- `initiated_by` → Customer (1:1, optional)
- `flagged_by_fraud_detection` → FraudAlert (1:many, optional)

**Example:**
```
Transaction: TXN-0000542
- account_id: ACC-00001
- transaction_type: Inbound Transfer
- amount: 150000 (£1,500.00 in pence)
- transaction_date: 2026-01-15
- settlement_date: 2026-01-15
- description: "Salary deposit"
- status: Completed
- reference: "SAL-JAN-2026"
```

---

### Entity: Standing Order

**Definition:** A recurring transfer initiated by the account holder.

**Attributes:**
- `standing_order_id` — Unique identifier
- `account_id` — Account initiating transfer
- `beneficiary_account_id` — Recipient account (if internal)
- `beneficiary_name` — Recipient name
- `beneficiary_account_number` — Recipient account number (external)
- `beneficiary_sort_code` — Recipient bank sort code (external)
- `amount` — Transfer amount
- `frequency` — Weekly, Monthly, Quarterly, Annual
- `start_date` — First transfer date
- `end_date` — Last transfer date (if applicable)
- `status` — Active, Paused, Completed, Cancelled
- `reference` — Payment reference (e.g., "Mortgage-Jan")

**Key Business Rules:**
- Standing order creates automatic transaction each period
- Amount and frequency cannot change (must cancel and create new)
- Standing order survives account holder changes (if joint)
- 30-day notice for cancellation (except fraud/closure)

**Relationships:**
- `on_account` → Account (1:many)
- `generates_transactions` → Transaction (1:many)

---

### Entity: Direct Debit

**Definition:** A recurring payment initiated by a third party (biller) authorized by the customer.

**Attributes:**
- `direct_debit_id` — Unique identifier
- `account_id` — Account paying
- `biller_name` — Billing organization
- `biller_code` — Biller's assigned code
- `reference` — Customer's account with biller (e.g., utility account number)
- `amount_type` — Fixed, Variable, Quarterly
- `expected_amount` — Expected payment amount
- `maximum_amount` — Highest amount allowed
- `frequency` — Monthly, Quarterly, Annual
- `start_date` — First payment date
- `end_date` — Last payment date (if applicable)
- `status` — Active, Paused, Cancelled
- `mandate_reference` — Direct debit mandate ID

**Key Business Rules:**
- Customer authorizes specific biller via DD mandate
- Biller cannot debit more than maximum amount without notice
- Customer can cancel DD up to 5 days before payment
- Failed DD can be retried (usually once)
- DD protected by Direct Debit Guarantee

**Relationships:**
- `on_account` → Account (1:many)
- `initiated_by` → Biller (external organization, 1:many)
- `generates_transactions` → Transaction (1:many)

---

## 5. Risk & Compliance Domain

### Entity: KYC Verification

**Definition:** Know Your Customer verification confirming customer identity and legitimacy.

**Attributes:**
- `kyc_id` — Unique identifier
- `customer_id` — Linked customer
- `verification_date` — Date of verification
- `verification_method` — Document, Biometric, Database
- `identity_verified` — Boolean (confirmed identity)
- `address_verified` — Boolean (confirmed address)
- `pep_screened` — Boolean (checked for Politically Exposed Persons)
- `adverse_media_checked` — Boolean (checked for negative news)
- `verification_status` — Verified, Incomplete, Rejected, Expired
- `next_review_date` — Date for KYC refresh (typically 3-5 years)
- `reviewer` — Staff member who performed KYC

**Key Business Rules:**
- KYC mandatory before account opening
- KYC refreshed periodically (every 3–5 years)
- High-risk customers require Enhanced Due Diligence (EDD)
- KYC documents retained for 6 years post-closure

**Relationships:**
- `for_customer` → Customer (1:1)
- `verified_documents` → IdentificationDocument (1:many)

---

### Entity: AML Screening

**Definition:** Anti-Money Laundering screening to detect financial crime risks.

**Attributes:**
- `screening_id` — Unique identifier
- `customer_id` — Customer being screened
- `screening_date` — Date screening performed
- `screening_lists` — Which lists searched (e.g., OFAC, INTERPOL, HMT)
- `matches_found` — Number of potential matches
- `match_details` — List of matches (name, score, list name)
- `risk_level` — Low, Medium, High
- `manual_review_required` — Boolean (escalation needed)
- `screening_status` — Passed, Flagged, Escalated
- `reviewer` — Staff member who reviewed

**Screening Lists Checked:**
- OFAC Sanctions List (US Treasury)
- UN Sanctions List
- EU Sanctions List
- HM Treasury Sanctions Designations
- INTERPOL Red Notices
- PEP (Politically Exposed Persons) databases
- Adverse media databases

**Key Business Rules:**
- AML screening mandatory at account opening
- Ongoing AML monitoring for high-risk customers
- Hit on sanctions list = automatic account freeze + SAR filing
- Failed screening = account application rejected

**Relationships:**
- `for_customer` → Customer (1:1)
- `triggers_suspicious_activity_report` → SAR (0:many)

---

### Entity: Suspicious Activity Report (SAR)

**Definition:** A report filed with National Crime Agency (NCA) for suspected financial crime.

**Attributes:**
- `sar_id` — Unique identifier
- `customer_id` — Customer involved
- `filing_date` — Date filed with NCA
- `nca_reference` — NCA assigned reference number
- `activity_type` — Money laundering, Fraud, Sanctions, Other
- `activity_description` — Detailed description of suspicious activity
- `amount_involved` — Money involved (if applicable)
- `evidence` — Supporting evidence summary
- `status` — Filed, Acknowledged, Investigated, Closed
- `internal_reviewer` — Staff member who filed SAR

**Example Activity Types:**
- Large cash deposit followed by immediate transfer out
- Multiple accounts opened with similar identity
- Transactions with sanctioned country
- Round-tripping (money in and out same day)

**Key Business Rules:**
- SAR must be filed within 30 days of suspicion
- Customer not informed (reporting confidential)
- Account may be frozen pending investigation
- Failure to file SAR = regulatory breach

**Relationships:**
- `for_customer` → Customer (1:many)
- `triggered_by_transactions` → Transaction (1:many)
- `based_on_aml_screening` → AML_Screening (0:1)

---

### Entity: Fraud Alert

**Definition:** A system-generated alert when fraud is detected.

**Attributes:**
- `alert_id` — Unique identifier
- `account_id` — Linked account
- `transaction_id` — Linked transaction (if applicable)
- `alert_type` — Unauthorized use, Card fraud, Account takeover, Rule violation
- `fraud_score` — Risk score (0–100, where 100 = definite fraud)
- `risk_level` — Low, Medium, High, Critical
- `detected_date` — Date alert generated
- `alert_status` — Auto-approved, Manual review, Blocked
- `action_taken` — Approved, Blocked, Under review, False positive
- `reviewer` — Financial Crime team member (if manual review)

**Example Fraud Patterns:**
- Transaction in different country within 2 hours of previous transaction
- Large ATM withdrawal (>£2,000) from unusual location
- Card used by different person
- Velocity check (>5 transactions in 1 hour)

**Key Business Rules:**
- High-risk alerts automatically block transaction
- Customer can dispute fraud (Fraud Policy)
- Bank reimburses unauthorized fraud within 10 days
- Fraud data feeds ML model for continuous improvement

**Relationships:**
- `on_account` → Account (1:many)
- `on_transaction` → Transaction (0:1)
- `triggers_investigation` → FraudInvestigation (1:1, optional)

---

## 6. Organization Domain

### Entity: Employee

**Definition:** A person employed by FirstUK Bank.

**Attributes:**
- `employee_id` — Unique identifier
- `first_name` — Given name
- `last_name` — Family name
- `department_id` — Department (see [[departments]])
- `job_title` — Job title (e.g., "Account Manager")
- `manager_id` — Direct manager (employee_id)
- `start_date` — Employment start date
- `status` — Active, On leave, Terminated
- `clearance_level` — Security clearance level

**Relationships:**
- `works_in` → Department (1:1)
- `reports_to` → Employee (1:1, optional)
- `manages` → Employee (1:many)
- `performs_kyc_verification` → KYC_Verification (1:many)
- `reviews_sar` → SAR (1:many, optional)

---

### Entity: Department

**Definition:** An organizational unit within FirstUK Bank (see [[departments]]).

**Attributes:**
- `department_id` — Unique identifier
- `department_name` — Department name (e.g., "Retail Banking")
- `head_id` — Department head (employee_id)
- `parent_department_id` — Parent department (for hierarchy)
- `function` — Primary function

**Related Departments:**
- Retail Banking (see [[departments]])
- Product Management (see [[departments]])
- Operations (see [[departments]])
- Customer Service (see [[departments]])
- Financial Crime (see [[departments]])
- Compliance (see [[departments]])
- Risk (see [[departments]])
- Technology (see [[departments]])
- Marketing (see [[departments]])
- Legal (see [[departments]])
- Internal Audit (see [[departments]])

---

## 7. Cross-Domain Relationships

### Relationship: Customer → Account → Transaction

**Path:** Customer holds Account which records Transaction

```
Customer [has] Account [contains] Transaction
Example:
Sarah Johnson [has] ACC-00001 (SmartSaver) [contains] TXN-542 (Salary deposit)
```

### Relationship: Product → Feature → Account

**Path:** Product offers Feature, which is available in Account

```
Product [has] Feature
Account [of] Product
Example:
SAV-001 (SmartSaver) [has] "Joint Account Support" 
ACC-00002 (Sarah+John joint) [of] SAV-001
```

### Relationship: Account → Card → Transaction → FraudAlert

**Path:** Card linked to Account, transaction can generate fraud alert

```
Account [has] Card [processes] Transaction [triggers] FraudAlert
Example:
ACC-00001 [has] CARD-00001 [processes] TXN-543 (£2,000 ATM) [triggers] FRAUD-091
```

### Relationship: Customer → KYC → AML_Screening → SAR

**Path:** Customer verification → AML screening → suspicious activity reporting

```
Customer [has] KYC_Verification [has] AML_Screening [triggers] SAR
Example:
CUST-00010 [has] KYC-015 [has] AML-020 [triggers] SAR-008
```

---

## Constraints & Business Rules

### Account Constraints

| Rule | Constraint |
|------|-----------|
| Account must have product | `account.product_id NOT NULL` |
| Account balance = transaction sum | `account.balance = SUM(transactions)` |
| Dormant > 12 months inactive | `account_status = 'Dormant' IF no_activity > 365 days` |
| Account status unique | `account.status IN (Active, Dormant, Closed, Suspended)` |

### Customer Constraints

| Rule | Constraint |
|------|-----------|
| Must have identity verified | `customer.kyc_status = 'Verified'` |
| Must pass AML screening | `customer.aml_status != 'Flagged'` |
| Must have valid address | `customer.address IS NOT NULL AND verified` |
| Age 18+ | `customer.age >= 18` |

### Transaction Constraints

| Rule | Constraint |
|------|-----------|
| Amount > 0 | `transaction.amount > 0` |
| Account must exist | `transaction.account_id REFERENCES account(account_id)` |
| Immutable post-posting | `transaction.status IN (Pending, Completed) AND cannot_edit_completed` |

### Compliance Constraints

| Rule | Constraint |
|------|-----------|
| SAR filed within 30 days | `SAR.filing_date <= suspicion_date + 30 days` |
| KYC refreshed 3-5 years | `KYC.next_review_date <= TODAY() + 5 years` |
| Fraud reimbursement 10 days | `Fraud_claim.reimbursement_date <= claim_date + 10 days` |

---

## Example: Complete Entity Instance

**Customer:** John Smith  
**Opening New Account:** SmartSaver with Joint Holder

```
PARTY:
  party_id: PARTY-00100
  party_type: Natural Person
  
CUSTOMER:
  customer_id: CUST-00050
  first_name: John
  last_name: Smith
  date_of_birth: 1980-05-20
  nationality: UK
  email: john.smith@email.com
  customer_status: Active
  
ADDRESS:
  address_id: ADDR-00150
  street: 42 Acacia Avenue
  city: Manchester
  postcode: M1 1AA
  address_type: Registered
  
IDENTIFICATION_DOCUMENT:
  doc_id: ID-00200
  doc_type: UK Driving License
  doc_number: SMITH802056M99AB
  expiry_date: 2032-05-20
  verification_status: Verified
  
KYC_VERIFICATION:
  kyc_id: KYC-00250
  customer_id: CUST-00050
  identity_verified: TRUE
  address_verified: TRUE
  pep_screened: TRUE
  verification_status: Verified
  
AML_SCREENING:
  screening_id: AML-00300
  customer_id: CUST-00050
  matches_found: 0
  risk_level: Low
  screening_status: Passed
  
ACCOUNT:
  account_id: ACC-00500
  product_id: SAV-001 (SmartSaver Account)
  opening_date: 2026-01-15
  account_status: Active
  account_balance: 0 (new account)
  
ACCOUNT_HOLDER (Primary):
  holder_id: AHLD-00600
  account_id: ACC-00500
  customer_id: CUST-00050
  holder_type: Primary
  
ACCOUNT_HOLDER (Joint):
  holder_id: AHLD-00601
  account_id: ACC-00500
  customer_id: CUST-00051 (Jane Smith)
  holder_type: Joint

TRANSACTION (First transaction: opening deposit):
  transaction_id: TXN-00700
  account_id: ACC-00500
  transaction_type: Deposit
  amount: 500000 (£5,000.00)
  transaction_date: 2026-01-15
  status: Completed
  description: Opening deposit

CARD:
  card_id: CARD-00800
  account_id: ACC-00500
  customer_id: CUST-00050
  card_type: Debit
  card_status: Active
  daily_limit: 500000 (£5,000)
```

---

## Related Standards & Frameworks

- **FIBO** (Financial Industry Business Ontology) — International standard for financial concepts
- **ISO 20022** — Financial data exchange standards
- **FCA Handbook** — Regulatory requirements
- **PRA Rulebook** — Capital and risk requirements

---

## Ontology Maintenance

**Review Frequency:** Quarterly  
**Change Control:** New entities/relationships require Architecture Review Board approval  
**Version Control:** All changes tracked in document history  

---

## Document Version History

| Version | Date | Change | Author |
|---------|------|--------|--------|
| 1.4 | 2026-01-15 | Added fraud and compliance entities | Enterprise Architect |
| 1.3 | 2025-09-01 | Added joint account entity | Enterprise Architect |
| 1.2 | 2025-06-01 | Added transaction types detail | Data Engineer |
| 1.1 | 2025-03-01 | Refined account constraints | Enterprise Architect |
| 1.0 | 2024-12-01 | Initial ontology version | Enterprise Architect |

---

## Sign-Off

**Approved by:**  
Chief Technology Officer — **Date: 2026-01-15**  
Enterprise Architect — **Date: 2026-01-15**

---
