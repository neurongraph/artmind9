# FirstUK Bank — Technology Systems & Architecture

## Metadata

| Field | Value |
|-------|-------|
| Document ID | SYS-2026-001 |
| Version | 2.1 |
| Effective Date | 2026-01-15 |
| Review Date | 2026-07-15 |
| Owner | Chief Technology Officer |
| Department | Technology |
| Status | Active |
| Classification | Internal |
| Audience | Technology Staff, Architects, Developers, Operations |
| Related Documents | [[organisation_model]], [[departments]], [[products]] |

---

## Executive Summary

FirstUK Bank operates an integrated technology architecture supporting retail banking operations across 12 branches and digital channels. The architecture emphasizes microservices, API-first design, and cloud-ready infrastructure while maintaining regulatory compliance and security.

**Core Systems:** 5 major systems  
**API Endpoints:** 45+ endpoints  
**Data Warehouse:** Real-time processing  
**System Availability:** 99.8% (SLA)  
**Uptime (2025):** 99.85%

---

## Technology Vision

**Strategic Direction:**
- Microservices-based, event-driven architecture
- API-first integration model
- Cloud-ready infrastructure (hybrid cloud)
- Real-time data processing and analytics
- Security-first design (zero-trust)
- Open Banking compliance (PSD2, Open Banking Standard)

**Regulatory Alignment:**
- FCA COBS (Conduct of Business)
- PRA Rulebook (capital, liquidity)
- GDPR (data protection)
- Open Banking regulations

---

## Application Landscape

### Core Banking Systems

#### 1. Account Management System (AMS)

**System ID:** AMS-001  
**Purpose:** Central repository for customer accounts, balances, and transactions  
**Technology:** Java microservices, PostgreSQL database  
**Deployment:** Cloud (AWS)  
**Availability:** 99.9% (SLA)  

**Key Functions:**
- Account creation and lifecycle
- Balance management
- Transaction recording
- Interest calculation
- Account status tracking

**Data Model:**
- Customers
- Accounts (see [[business_ontology]])
- Account Holders (primary, secondary, joint)
- Transactions
- Account Status

**APIs Exposed:**
- `POST /accounts` — Create account
- `GET /accounts/{accountId}` — Retrieve account details
- `GET /accounts/{accountId}/balance` — Get balance
- `GET /accounts/{accountId}/transactions` — Transaction history
- `POST /accounts/{accountId}/transactions` — Record transaction

**Integration:**
- Internet Banking Platform (IBP)
- Mobile Banking App
- Fraud Detection Engine
- Data Warehouse

**Database:**
- PostgreSQL 13+
- 800 accounts × 2,000+ transactions = 1.6M records
- Backup: Daily encrypted backup to S3
- Recovery Point Objective (RPO): 15 minutes

---

#### 2. Internet Banking Platform (IBP)

**System ID:** IBP-001  
**Purpose:** Web-based customer portal for account access and self-service  
**Technology:** React.js frontend, Node.js backend  
**Deployment:** Cloud (AWS)  
**Availability:** 99.8% (SLA)  

**Key Features:**
- Secure login (username/password + OTP)
- Account information dashboard
- Transaction history and search
- Standing orders and direct debits
- Beneficiary management
- Money transfer
- E-statements
- Profile and settings management

**Security:**
- TLS 1.3 encryption
- Session management (15-min idle timeout)
- Two-factor authentication (OTP via email/SMS)
- XSS/CSRF protection
- SQL injection prevention

**Usage:**
- Daily active users: 200–300
- Peak traffic: 10:00–12:00 GMT
- Response time: <2 seconds (p95)

**Integration:**
- Account Management System (AMS) — account data
- Fraud Detection Engine — transaction validation
- Payment Processing System (PPS) — transfer execution

---

#### 3. Mobile Banking App

**System ID:** MBA-001  
**Purpose:** Native mobile application for iOS and Android  
**Technology:** React Native, Node.js backend  
**Deployment:** Cloud (AWS) + CDN (CloudFront)  
**Availability:** 99.5% (SLA)  

**Key Features:**
- Secure login (biometric + PIN)
- Account viewing
- Mobile-optimized transaction history
- Push notifications for transactions
- Mobile money transfer
- Card management (lock/unlock)
- Virtual card numbers (for online shopping)
- Location-aware services

**Mobile Specifications:**
- iOS: Version 13+
- Android: Version 9+
- App Size: ~50MB
- Download Method: Apple App Store, Google Play

**Biometric Authentication:**
- Face ID (iOS)
- Touch ID (iOS)
- Fingerprint (Android)
- Fallback: PIN authentication

**Push Notifications:**
- Transaction alerts
- Security alerts
- Marketing messages
- Service notifications

**Usage:**
- Daily active users: 150–200
- Key actions: Balance check, transfer, card lock

**Integration:**
- Account Management System (AMS)
- Fraud Detection Engine
- Push Notification Service (PNS)

---

#### 4. Fraud Detection Engine (FDE)

**System ID:** FDE-001  
**Purpose:** Real-time detection and prevention of fraudulent transactions  
**Technology:** Python, Apache Kafka, TensorFlow (ML models)  
**Deployment:** Cloud (AWS) + on-premises (for sensitive rules)  
**Latency:** <100ms per transaction  

**Key Functions:**
- Transaction risk scoring
- Anomaly detection (ML models)
- Rule-based detection
- Card fraud prevention
- Account takeover detection
- Suspicious activity flagging

**Fraud Rules (Examples):**
- Large transaction amount (>£5,000)
- Geographic anomaly (transaction in different country within 2 hours)
- Velocity checks (>5 transactions within 1 hour)
- Unusual beneficiary (new beneficiary + large transfer)
- Card not present (CNP) high-value transaction

**Machine Learning Models:**
- Transaction Risk Model (trained on 2,000+ fraud cases)
- Anomaly Detection Model (unsupervised)
- Chargeback Prediction Model

**Alert Escalation:**
- Low Risk: Auto-approve
- Medium Risk: Require OTP verification
- High Risk: Block transaction, notify customer, escalate to Financial Crime team (see [[departments]])

**Performance:**
- Detection Rate: 97%+
- False Positive Rate: <2%
- Monthly fraud prevented: ~£45k–£80k

**Integration:**
- Account Management System (AMS) — transaction data
- Internet Banking Platform (IBP) — real-time blocking
- Mobile Banking App (MBA)
- Payment Processing System (PPS)
- Financial Crime System — SAR filing

---

#### 5. Payment Processing System (PPS)

**System ID:** PPS-001  
**Purpose:** Process customer payments and inter-bank settlements  
**Technology:** Java microservices, message queue (RabbitMQ)  
**Deployment:** Cloud (AWS)  
**Processing: 24/7 real-time + batch overnight (BACS)

**Key Functions:**
- Transfer initiation and validation
- Standing order processing
- Direct debit processing
- Settlement to other banks
- Reconciliation and reporting

**Payment Methods Supported:**
- Faster Payments Service (FPS) — Real-time, up to £1M
- BACS — Overnight, high volume
- CHAPS — High-value, real-time
- Internal transfers (same bank)

**Batch Processing Schedule:**
- FPS: Real-time (immediate)
- BACS Submission: 20:00 GMT daily
- CHAPS: Real-time (during hours)

**Performance:**
- FPS processing: <15 minutes
- BACS processing: Next business day
- Success rate: 99.95%
- Failed transactions: <5 per day

**Integration:**
- Account Management System (AMS) — account validation
- Fraud Detection Engine (FDE) — fraud check
- Bank of England Settlement Systems (external)
- Treasury System — reconciliation

---

### Data & Analytics Systems

#### Data Warehouse (DW)

**System ID:** DW-001  
**Purpose:** Centralized analytics and business intelligence  
**Technology:** AWS Redshift, Apache Spark  
**Data Model:** Star schema (facts + dimensions)  
**Update Frequency:** Real-time (for hot data), nightly batch (historical)

**Key Data Marts:**
- **Customer Mart:** 500 customers × 50 attributes
- **Account Mart:** 800 accounts × attributes
- **Transaction Mart:** 2,000+ monthly transactions
- **Product Mart:** 5 products × performance metrics
- **Compliance Mart:** KYC, AML, complaints data

**Dashboards & Reports:**
- Executive Dashboard (daily KPIs)
- Product Performance Dashboard
- Fraud Detection Dashboard
- Compliance & Risk Dashboard
- Customer Analytics Dashboard

**Retention Policy:**
- Hot data (3 months): Real-time
- Warm data (1–3 years): Nightly batch
- Cold data (3+ years): Archive (S3)

---

### Supporting Systems

#### Document Management System (DMS)

**System ID:** DMS-001  
**Purpose:** Store and manage documents (policies, procedures, templates)  
**Technology:** Elasticsearch, S3  
**Document Types:**
- Policies (see [[departments]])
- Procedures (SOPs)
- Templates (letters, emails, SMS)
- Reports
- Audit documents

---

#### Customer Relationship Management (CRM)

**System ID:** CRM-001  
**Purpose:** Customer interaction and relationship tracking  
**Technology:** Salesforce CRM (SaaS)  
**Deployment:** Cloud (Salesforce)  

**Key Functions:**
- Customer contact information
- Interaction history (calls, emails)
- Complaint tracking
- Marketing campaign tracking
- Customer feedback

**Integration:**
- Account Management System (AMS)
- Email system
- Phone system

---

#### Reporting & Regulatory System

**System ID:** RRS-001  
**Purpose:** Generate regulatory reports for FCA/PRA  
**Technology:** Python, XBRL toolkit  
**Deployment:** On-premises (secured)

**Key Reports:**
- CASS (Client Assets sourcebook) report
- COREP (Capital requirement and Exposure report)
- FINREP (Financial report)
- AML reports (suspicious activity)

---

## System Context Diagram

```
┌─────────────────────────────────────────────────────────┐
│                    External Systems                      │
├─────────────────────────────────────────────────────────┤
│  Bank of England Settlement Systems                      │
│  Payment Scheme Operators (FPS, BACS, CHAPS)            │
│  Regulatory Authorities (FCA, PRA)                      │
└─────────────────────────────────────────────────────────┘
                          │
        ┌─────────────────┼─────────────────┐
        │                 │                 │
    ┌───▼────┐     ┌─────▼──────┐   ┌─────▼──────┐
    │  IBP   │     │   Mobile   │   │   Branch   │
    │ Portal │     │     App    │   │  Systems   │
    └───┬────┘     └─────┬──────┘   └─────┬──────┘
        │                 │                 │
        └─────────────────┼─────────────────┘
                          │
                    ┌─────▼──────────┐
                    │  API Gateway   │
                    │   (Kong)       │
                    └─────┬──────────┘
                          │
        ┌─────────────────┼─────────────────────────┐
        │                 │                         │
    ┌───▼────┐  ┌────────▼──────┐  ┌──────────────▼─┐
    │  AMS   │  │  Fraud        │  │  Payment       │
    │        │  │  Detection    │  │  Processing    │
    └────────┘  │  Engine       │  │  System        │
                └───────────────┘  └────────────────┘
                
                    ┌──────────────┐
                    │ Data         │
                    │ Warehouse    │
                    └──────────────┘
```

---

## API Catalogue

### Account APIs

| Endpoint | Method | Purpose | Auth |
|----------|--------|---------|------|
| `/accounts` | POST | Create account | API Key + JWT |
| `/accounts/{id}` | GET | Get account details | JWT |
| `/accounts/{id}/balance` | GET | Get balance | JWT |
| `/accounts/{id}/transactions` | GET | List transactions | JWT |
| `/accounts/{id}/transactions` | POST | Record transaction | API Key (internal) |
| `/accounts/{id}/standing-orders` | GET/POST | Standing orders | JWT |
| `/accounts/{id}/direct-debits` | GET/POST | Direct debits | JWT |

### Payment APIs

| Endpoint | Method | Purpose | Auth |
|----------|--------|---------|------|
| `/payments/fps` | POST | FPS transfer | JWT |
| `/payments/bacs` | POST | BACS transfer | JWT |
| `/payments/validate` | POST | Validate payment | JWT |

### Customer APIs

| Endpoint | Method | Purpose | Auth |
|----------|--------|---------|------|
| `/customers` | POST | Create customer | API Key + JWT |
| `/customers/{id}` | GET | Get customer details | JWT |
| `/customers/{id}/kyc` | GET/POST | KYC verification | API Key (internal) |
| `/customers/{id}/aml-screening` | POST | AML screening | API Key (internal) |

### Card APIs

| Endpoint | Method | Purpose | Auth |
|----------|--------|---------|------|
| `/cards/{id}/lock` | POST | Lock card | JWT |
| `/cards/{id}/unlock` | POST | Unlock card | JWT |
| `/cards/{id}/replace` | POST | Replace card | JWT |

### Fraud APIs

| Endpoint | Method | Purpose | Auth |
|----------|--------|---------|------|
| `/fraud/score` | POST | Score transaction | API Key (internal) |
| `/fraud/alerts` | GET | List alerts | API Key (internal) |

### Reporting APIs

| Endpoint | Method | Purpose | Auth |
|----------|--------|---------|------|
| `/reports/customers` | GET | Customer report | API Key (internal) |
| `/reports/transactions` | GET | Transaction report | API Key (internal) |
| `/reports/compliance` | GET | Compliance report | API Key (internal) |

---

## Integration Architecture

### Event-Driven Integration (Asynchronous)

**Message Queue:** Apache Kafka

**Key Topics:**
- `account.created` — New account opened
- `transaction.recorded` — Transaction completed
- `account.closed` — Account closed
- `fraud.alert` — Fraud detection alert
- `kyc.completed` — KYC verification done
- `standing-order.processed` — Standing order executed

**Example Flow:**
```
1. Customer initiates transfer via IBP
2. PPS validates transfer → `transfer.initiated` event
3. FDE checks fraud → `fraud.scored` event
4. If approved: PPS processes → `transfer.completed` event
5. AMS updates balance → `balance.updated` event
6. DW updates data → analytics available in 5 min
```

### Synchronous Integration (APIs)

**API Gateway:** Kong (manages authentication, rate limiting, monitoring)

**Endpoints:** 45+ RESTful API endpoints (see above)

**Authentication Methods:**
- JWT (session-based, for customers)
- API Key (for internal services)
- OAuth 2.0 (future: for third-party integrations)

---

## Database Architecture

### Primary Database (PostgreSQL)

**Database:** `firstuk_prod`

**Schemas:**
- `public.accounts` — Account records (800 records)
- `public.customers` — Customer records (500 records)
- `public.transactions` — Transaction log (2,000+ records/month)
- `public.account_holders` — Joint account holders
- `public.standing_orders` — Recurring transfers
- `public.direct_debits` — Recurring bill payments
- `public.cards` — Debit card records (800 cards)
- `public.compliance_kyc` — KYC verification records
- `public.compliance_aml` — AML screening records
- `public.products` — Product definitions (5 products)

**Indexes:**
- Customer ID (for lookups)
- Account ID (for queries)
- Transaction date (for history)
- Account status (for reporting)

**Backup Strategy:**
- Continuous replication to standby database
- Daily encrypted backup to AWS S3
- RPO: 15 minutes
- RTO: 2 hours

### Caching Layer (Redis)

**Purpose:** Reduce database load, improve response times

**Cache Keys:**
- `account:{accountId}:balance` — Current balance (5-min TTL)
- `customer:{customerId}:profile` — Customer info (10-min TTL)
- `fraud-rules` — Fraud detection rules (1-hour TTL)

---

## Security Architecture

### Authentication & Authorization

**Customer Authentication:**
- Internet Banking: Username/password + OTP (email/SMS)
- Mobile App: Biometric (fingerprint/face) + optional PIN
- Branch: Photo ID verification

**Internal Authentication:**
- Staff: LDAP/Active Directory
- API Services: Mutual TLS (mTLS) + JWT
- Admin Access: Multi-factor authentication (MFA)

**Authorization:**
- Role-Based Access Control (RBAC)
- Attribute-Based Access Control (ABAC) for sensitive operations
- Audit logging for all access

### Data Encryption

**In Transit:**
- TLS 1.3 for all external connections
- mTLS for internal service-to-service communication

**At Rest:**
- AES-256 encryption for database backups
- Encrypted S3 buckets
- Encrypted file systems

### Secrets Management

**Tool:** AWS Secrets Manager

**Secrets:**
- Database credentials
- API keys
- TLS certificates
- Integration passwords

**Rotation:** Automatic (90-day rotation for credentials)

### Network Security

**Firewall Rules:**
- Ingress: Port 443 (HTTPS) only for public APIs
- Branch systems: VPN-only access
- Admin access: Restricted IP whitelist

**DDoS Protection:**
- AWS Shield (managed DDoS protection)
- CloudFlare (optional, if needed)

**Intrusion Detection:**
- AWS GuardDuty (threat detection)
- Log analysis and alerting

---

## Disaster Recovery & Business Continuity

### Recovery Objectives

| Metric | Target |
|--------|--------|
| Recovery Time Objective (RTO) | 2 hours |
| Recovery Point Objective (RPO) | 15 minutes |
| Backup Frequency | Daily + continuous replication |

### Backup Strategy

**Database Backups:**
- Continuous replication to standby (real-time)
- Daily encrypted backup to S3 (retention: 30 days)
- Weekly backup to cold storage (retention: 1 year)

**Application Backups:**
- Source code in Git (GitHub)
- Docker images in registry (AWS ECR)
- Configuration in version control

### Disaster Recovery Testing

**Frequency:** Quarterly (at least)

**Test Scenarios:**
- Database restoration from backup
- Application failover to standby region
- Network failure simulation
- Data loss scenario

---

## Performance & Scalability

### Current Load

| System | Daily Transactions | Peak QPS | Response Time (p95) |
|--------|-------------------|----------|-------------------|
| IBP | 500–700 | 5 | <2s |
| MBA | 300–500 | 3 | <2s |
| AMS | 2,000+ | 10 | <100ms |
| PPS | 100–200 | 2 | <15min (batch) |
| FDE | 2,000+ | 20 | <100ms |

### Scalability

**Auto-Scaling:**
- Microservices: Auto-scale based on CPU (target: 70%)
- Database: Read replicas for read-heavy operations
- API Gateway: Managed by Kong (can scale to 1000+ QPS)

**Performance Optimization:**
- Caching (Redis) for frequently accessed data
- Database indexes on key columns
- Asynchronous processing (Kafka) for non-critical operations

---

## Compliance & Regulatory

### Regulatory Requirements

**FCA COBS:**
- Customer communication and fair treatment
- System reliability and business continuity
- Data security and privacy

**PRA Requirements:**
- Capital adequacy monitoring
- Operational resilience
- Cyber security (Operational Resilience)

**GDPR:**
- Data subject rights (access, deletion)
- Data protection by design
- Breach notification (72-hour requirement)

### System Audit & Monitoring

**Logging:**
- Centralized logging (AWS CloudWatch)
- Audit logs for all transactions
- Access logs for compliance

**Monitoring:**
- System health dashboards
- Performance monitoring (CloudWatch, New Relic)
- Security monitoring (CloudTrail, GuardDuty)

**Incident Response:**
- Escalation procedure (see [[departments]])
- Post-incident review (PIR)
- Continuous improvement

---

## Technology Roadmap

### 2026 Initiatives

| Initiative | Timeline | Status | Impact |
|-----------|----------|--------|--------|
| Mobile App v2.0 (redesign) | Q2 2026 | Planning | Enhanced UX |
| Open Banking (PSD2) API | Q3 2026 | Design | Third-party integration |
| Blockchain Settlement (pilot) | Q4 2026 | Research | Faster settlement |

### 2027 Initiatives

| Initiative | Timeline | Expected | Impact |
|-----------|----------|----------|--------|
| Video Account Opening | Q1 2027 | Design | Digital-first onboarding |
| AI-Powered Chat Bot | Q2 2027 | Scoping | Customer support automation |
| Advanced Analytics Platform | Q3 2027 | Research | Predictive analytics |

---

## Technology Budget

**Annual Technology Budget:** £800k

| Category | Budget | Allocation |
|----------|--------|-----------|
| Cloud Infrastructure (AWS) | £300k | 37.5% |
| Staff & Contractors | £300k | 37.5% |
| Tools & Licenses | £100k | 12.5% |
| Training & Development | £50k | 6.25% |
| Contingency | £50k | 6.25% |

---

## Related Documents

- [[organisation_model]] — Technology department structure
- [[departments]] — Technology department details
- [[products]] — Product specifications
- Application Landscape (APP-LAND-001)
- Logical Data Model (LDM-001)
- Integration Specification (INT-SPEC-001)
- Information Security Policy (SEC-POL-002)
- Disaster Recovery Plan (DRP-001)

---

## Sign-Off

**Approved by:**  
Chief Technology Officer — **Date: 2026-01-15**  
Chief Information Security Officer — **Date: 2026-01-15**

---
